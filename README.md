# workout-watch

Apple Watch custom workouts (`.workout`) for Bram's Rotterdam Marathon 2027 plan.

- `w/YYYY-MM-DD.workout`: one file per structured session, built from the plan by the `/strava` skill (`tools/build.py build`).
- `w/next.workout`: copy of the next session (today or later, Amsterdam time). Moved forward every night by `.github/workflows/next.yml`.
- `w/index.json`: every workout with date and title. `w/next.json`: which one `next.workout` holds.

On the iPhone the "Add to Watch" Shortcut fetches
`https://raw.githubusercontent.com/Mr-NL01/workout-watch/main/w/next.workout` and opens it in Fitness.

File format: reverse-engineered Protocol Buffers, see `tools/workout_codec.py`. Not documented by Apple; a watchOS update can change it.
