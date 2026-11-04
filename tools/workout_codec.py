"""Encode/decode Apple Watch custom workout (.workout) files.

Reverse-engineered from a real export (iOS/watchOS, October 2026).
Format: Protocol Buffers, no compression. Only the fields seen so far are supported.
"""
import struct
import sys
import uuid as uuidlib


# ---------- low-level protobuf helpers ----------
def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def key(field, wire):
    return varint((field << 3) | wire)


def f_varint(field, n):
    return key(field, 0) + varint(n)


def f_bytes(field, b):
    return key(field, 2) + varint(len(b)) + b


def f_double(field, x):
    return key(field, 1) + struct.pack("<d", x)


# ---------- known enums ----------
ACTIVITY_RUNNING = 37      # HKWorkoutActivityType.running
LOCATION_OUTDOOR = 3       # HKWorkoutSessionLocationType.outdoor
PURPOSE_WORK = 1
PURPOSE_RECOVERY = 2
GOAL_TIME = 1
GOAL_DISTANCE = 3
GOAL_OPEN = 4
UNIT_SECONDS = 1           # time quantities
UNIT_METERS = 1            # distance quantities
UNIT_KILOMETERS = 2        # distance quantities
ALERT_SPEED_THRESHOLD = 2  # single pace value
ALERT_HEART_RATE_RANGE = 5 # min-max bpm


# ---------- workout building blocks ----------
def goal_open():
    return f_varint(1, GOAL_OPEN)


def goal_time(seconds):
    quantity = f_varint(1, UNIT_SECONDS) + f_double(2, float(seconds))
    return f_varint(1, GOAL_TIME) + f_bytes(2, quantity)


def goal_distance(value, unit=UNIT_METERS):
    # distance quantity lives in field 4 (time uses field 2)
    quantity = f_varint(1, unit) + f_double(2, float(value))
    return f_varint(1, GOAL_DISTANCE) + f_bytes(4, quantity)


def _qty(unit, value):
    return f_varint(1, unit) + f_double(2, float(value))


def alert_pace(sec_per_km, metric=1):
    """Pace alert with one target pace. Stored as speed: X meters per 1 second.
    metric: 1 as seen in the export (probably current or average pace, unverified)."""
    mps = 1000 / sec_per_km
    speed = f_bytes(1, _qty(UNIT_METERS, mps)) + f_bytes(2, _qty(UNIT_SECONDS, 1.0))
    return (f_varint(1, ALERT_SPEED_THRESHOLD) + f_varint(2, metric)
            + f_bytes(4, f_bytes(1, speed)))


def alert_heart_rate(low_bpm, high_bpm, metric=2):
    rng = f_bytes(1, f_double(1, float(low_bpm))) + f_bytes(2, f_double(1, float(high_bpm)))
    return (f_varint(1, ALERT_HEART_RATE_RANGE) + f_varint(2, metric)
            + f_bytes(7, f_bytes(2, rng)))


def step(goal, alert=None, name=None):
    # WorkoutStep { 1: Goal, 2: Alert, 3: display name }
    body = f_bytes(1, goal)
    if alert is not None:
        body += f_bytes(2, alert)
    if name is not None:
        body += f_bytes(3, name.encode())
    return body


def interval_step(purpose, goal, alert=None, name=None):
    # IntervalStep { 1: purpose, 2: WorkoutStep }
    return f_varint(1, purpose) + f_bytes(2, step(goal, alert, name))


def block(steps, iterations):
    # IntervalBlock { 1: IntervalStep (repeated), 2: iterations }
    body = b"".join(f_bytes(1, s) for s in steps)
    return body + f_varint(2, iterations)


def custom_workout(name, blocks, warmup=None, cooldown=None,
                   activity=ACTIVITY_RUNNING, location=LOCATION_OUTDOOR):
    """warmup / cooldown: a goal, or a full step(...) wrapped via Step(...)."""
    body = f_varint(1, activity) + f_varint(2, location) + f_bytes(3, name.encode())
    if warmup is not None:
        body += f_bytes(4, warmup.data if isinstance(warmup, Step) else step(warmup))
    for b in blocks:
        body += f_bytes(5, b)
    if cooldown is not None:
        body += f_bytes(6, cooldown.data if isinstance(cooldown, Step) else step(cooldown))
    return body


class Step:
    """Use for a warm-up or cool-down that needs an alert or name:
    warmup=Step(goal_distance(1000), alert=alert_pace(360))"""
    def __init__(self, goal, alert=None, name=None):
        self.data = step(goal, alert, name)


def workout_file(name, blocks, warmup=None, cooldown=None, wid=None):
    wid = wid or str(uuidlib.uuid4()).upper()
    return (f_bytes(9, wid.encode())
            + f_bytes(11, custom_workout(name, blocks, warmup, cooldown))
            + f_varint(1000, 1)      # unknown, constant 1 (format version?)
            + f_varint(1002, 5))     # unknown, constant 5


# ---------- generic decoder (for inspecting new files) ----------
def decode(buf, depth=0, out=None):
    out = out if out is not None else []
    i = 0
    pad = "  " * depth
    while i < len(buf):
        k, i = _read_varint(buf, i)
        field, wire = k >> 3, k & 7
        if wire == 0:
            v, i = _read_varint(buf, i)
            out.append(f"{pad}{field}: {v}")
        elif wire == 1:
            v = struct.unpack("<d", buf[i:i + 8])[0]
            i += 8
            out.append(f"{pad}{field}: {v} (double)")
        elif wire == 5:
            v = struct.unpack("<f", buf[i:i + 4])[0]
            i += 4
            out.append(f"{pad}{field}: {v} (float)")
        elif wire == 2:
            n, i = _read_varint(buf, i)
            chunk = buf[i:i + n]
            i += n
            try:
                text = chunk.decode('utf-8')
                if text and text.isprintable():
                    out.append(f"{pad}{field}: {text!r}")
                    continue
            except UnicodeDecodeError:
                pass
            try:
                sub = []
                decode(chunk, depth + 1, sub)
                out.append(f"{pad}{field} {{")
                out.extend(sub)
                out.append(f"{pad}}}")
            except Exception:
                out.append(f"{pad}{field}: {chunk.decode('utf-8', 'replace')!r}")
        else:
            raise ValueError(f"unknown wire type {wire}")
    return out


def _read_varint(buf, i):
    shift = n = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, i


if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read()
    print("\n".join(decode(data)))
