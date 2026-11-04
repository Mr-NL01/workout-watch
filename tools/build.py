"""Build Apple Watch .workout files for Bram's Rotterdam Marathon 2027 plan.

  python3 tools/build.py build --schedule rotterdam-marathon-2027-schedule.md [--today YYYY-MM-DD]
      Writes w/YYYY-MM-DD.workout for every session with a `workout` block,
      w/index.json (all of them, by date), removes dated files no longer in the
      schedule, then runs `next`. Needs PyYAML. Run by the /strava skill.

  python3 tools/build.py next [--today YYYY-MM-DD]
      Copies the first workout dated today or later (Europe/Amsterdam) to
      w/next.workout and describes it in w/next.json. If nothing is upcoming,
      next.workout is left as it is. Run nightly by the GitHub Action.

File rules match the dashboard's encoder: title "DD/MM - Title", open warm-up and
cool-down with the expected length as the step name, no pace alerts on steps
under 2 minutes. The workout UUID is derived from date and title, so a rebuild of
an unchanged session gives the same bytes.
"""
import argparse, datetime, json, os, re, shutil, sys, uuid
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from workout_codec import (Step, alert_heart_rate, alert_pace, block, goal_distance, goal_open,
                           goal_time, interval_step, workout_file, PURPOSE_RECOVERY, PURPOSE_WORK,
                           UNIT_METERS)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = os.path.join(ROOT, "w")
DATED = re.compile(r"^\d{4}-\d{2}-\d{2}\.workout$")


def today_ams():
    return datetime.datetime.now(ZoneInfo("Europe/Amsterdam")).date().isoformat()


def pace_s(p):
    m, s = str(p).split(":")
    return int(m) * 60 + int(s)


def to_alert(a, step_s=None):
    if not a:
        return None
    if a.get("hr"):
        return alert_heart_rate(a["hr"][0], a["hr"][1])
    if a.get("pace") and not (step_s and step_s < 120):
        return alert_pace(pace_s(a["pace"]))
    return None


def to_goal(s):
    if s.get("time_s") is not None:
        return goal_time(s["time_s"])
    if s.get("distance_m") is not None:
        return goal_distance(s["distance_m"], UNIT_METERS)
    return goal_open()


def session_file(s):
    w = s["workout"]
    y, mo, d = s["date"].split("-")
    title = f"{d}/{mo} - {s['title']}"
    wid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"rotterdam-2027/{s['date']}/{s['title']}")).upper()
    opened = lambda part: Step(goal_open(), alert=to_alert(part.get("alert")), name=part.get("name")) if part else None
    blocks = [block([interval_step(PURPOSE_RECOVERY if st["purpose"] == "recovery" else PURPOSE_WORK,
                                   to_goal(st), alert=to_alert(st.get("alert"), st.get("time_s")),
                                   name=st.get("name")) for st in b["steps"]], b["repeat"])
              for b in w["blocks"]]
    return title, workout_file(title, blocks, opened(w.get("warmup")), opened(w.get("cooldown")), wid=wid)


def parse_schedule(text):
    import yaml
    sessions = []
    for m in re.finditer(r"```ya?ml\n(.*?)```", text, re.S):
        wk = yaml.safe_load(m.group(1)) or {}
        for s in wk.get("sessions") or []:
            s = dict(s)
            s["date"] = s["date"].isoformat() if hasattr(s["date"], "isoformat") else str(s["date"])
            if s.get("workout"):
                sessions.append(s)
    return sorted(sessions, key=lambda s: s["date"])


def cmd_build(schedule, today):
    os.makedirs(W, exist_ok=True)
    sessions = parse_schedule(open(schedule, encoding="utf-8").read())
    seen = {}
    for s in sessions:
        if s["date"] in seen:
            sys.exit(f"two workouts on {s['date']}; the repo keeps one file per date")
        seen[s["date"]] = s
    index, written = [], 0
    for date, s in seen.items():
        title, data = session_file(s)
        path = os.path.join(W, f"{date}.workout")
        if not os.path.exists(path) or open(path, "rb").read() != data:
            open(path, "wb").write(data)
            written += 1
        index.append({"date": date, "title": title, "file": f"w/{date}.workout", "bytes": len(data)})
    removed = [f for f in os.listdir(W) if DATED.match(f) and f[:10] not in seen]
    for f in removed:
        os.remove(os.path.join(W, f))
    json.dump(index, open(os.path.join(W, "index.json"), "w"), indent=1)
    nxt = cmd_next(today)
    return {"workouts": len(index), "written": written, "removed": removed, "next": nxt}


def cmd_next(today):
    index = json.load(open(os.path.join(W, "index.json")))
    up = [e for e in index if e["date"] >= today]
    if not up:
        return None
    e = up[0]
    shutil.copyfile(os.path.join(ROOT, e["file"]), os.path.join(W, "next.workout"))
    json.dump({"date": e["date"], "title": e["title"]},
              open(os.path.join(W, "next.json"), "w"), indent=1)
    return e["date"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "next"])
    ap.add_argument("--schedule")
    ap.add_argument("--today", default=None)
    a = ap.parse_args()
    t = a.today or today_ams()
    out = cmd_build(a.schedule, t) if a.cmd == "build" else {"next": cmd_next(t)}
    print(json.dumps(out))
