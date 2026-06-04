# Hevy Sync Service

Pulls strength training workouts and routines from Hevy into PostgreSQL.

---

## What it syncs

### Workouts (completed sessions)

| Table | Contents |
|-------|----------|
| `hevy_workouts` | Workout metadata: name, start/end time, duration |
| `hevy_workout_exercises` | Exercises per workout with order and notes |
| `hevy_workout_sets` | Per-set data: weight, reps, RPE, set type, timestamps |

### Routines (planned workouts)

| Table | Contents |
|-------|----------|
| `hevy_routines` | Routine names and metadata |
| `hevy_routine_exercises` | Exercises in each routine |
| `hevy_routine_sets` | Target sets/reps/weight per exercise |

All writes use upsert — safe to re-run.

---

## Configuration

```env
HEVY_API_KEY=your_api_key

DB_HOST=10.10.0.10
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=your_password
```

---

## Schedule

Runs every 6 hours via n8n Container Sync Schedules. Also runs on iOS Shortcut trigger and after deploy.

Note: Hevy has no webhook API — polling is the only option.

---

## hevy2garmin integration

After hevy-sync runs, the separate `hevy2garmin` container reads Hevy workout data and enriches the corresponding Garmin Connect activity with set/rep/weight detail. This runs every 30 minutes.

The enriched Garmin FIT is then downloaded and uploaded to Strava by `strava_replace_activity.py`, replacing the generic activity.

---

## Analytics layer

Views built on top of the sync tables:

| View | Purpose |
|------|---------|
| `hevy_weekly_volume` | Weekly set count, volume load, avg RPE |
| `hevy_exercise_progression` | Top weight, estimated 1RM, volume trends per exercise |
| `hevy_muscle_group_fatigue` | Fatigue score by muscle group and week |

### Muscle group mapping

Exercise → muscle group mapping lives in `hevy_exercise_muscle_map`. Uses exact name matching.

Find unmapped exercises:
```sql
SELECT DISTINCT e.title
FROM hevy_workout_exercises e
LEFT JOIN hevy_exercise_muscle_map m ON e.title = m.exercise_name
WHERE m.exercise_name IS NULL
ORDER BY e.title;
```

Add a mapping:
```sql
INSERT INTO hevy_exercise_muscle_map (exercise_name, primary_muscle_group)
VALUES ('Squat (Barbell)', 'quads')
ON CONFLICT (exercise_name) DO UPDATE
SET primary_muscle_group = EXCLUDED.primary_muscle_group;
```

### Fatigue score

```
fatigue_score = (weight * reps) * (RPE / 10)
```

---

## Files

| File | Purpose |
|------|---------|
| `hevy_sync.py` | Main sync script |
| `Dockerfile` | Container definition |
| `requirements.txt` | Python deps |

---

## Git safety

Do not commit: `.env`, API keys
