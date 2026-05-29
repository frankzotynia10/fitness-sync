# Hevy Sync Service

This service syncs strength training routines and workouts from Hevy into Postgres.

It powers the strength analytics layer, including:
- weekly training volume
- exercise progression
- muscle group fatigue
- integration with recovery and cycling data

---

## What this service does

### Routines (planned workouts)
Stored in:
hevy_routines
hevy_routine_exercises
hevy_routine_sets

Includes:
- routine names
- exercise order
- sets / reps / target weight

---

### Workouts (completed sessions)
Stored in:
hevy_workouts
hevy_workout_exercises
hevy_workout_sets

Includes per-set data:
- weight
- reps
- RPE
- timestamps

---

## Configuration

Create a file:
hevy/.env

Example:

HEVY_API_KEY=your_api_key_here

DB_HOST=your_db_host
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=your_password_here

---

## Running locally

From the hevy directory:

python hevy_sync.py

---

## Docker usage

Build:

docker build -t hevy-sync:latest .

Run:

docker run --env-file .env hevy-sync:latest

---

## Analytics layer

### Weekly volume
View: hevy_weekly_volume

Tracks:
- workout count
- total sets
- total volume load
- average RPE

---

### Exercise progression
View: hevy_exercise_progression

Tracks:
- top weight
- estimated 1RM
- volume per session
- RPE trends

---

### Muscle group fatigue
View: hevy_muscle_group_fatigue

Aggregates:
- set count
- volume load
- average RPE
- fatigue score

Grouped by:
- week
- muscle group

---

## Exercise to muscle group mapping

Required table:

hevy_exercise_muscle_map

Structure:
exercise_name TEXT PRIMARY KEY
primary_muscle_group TEXT NOT NULL

---

### Example mappings

('Bench Press (Barbell)', 'chest')
('Romanian Deadlift (Barbell)', 'hamstrings')
('Lateral Raise (Dumbbell)', 'shoulders')

---

## Key design decision

Mapping uses exact name matching.

Example:
Bench Press != Bench Press (Barbell)

This keeps the system:
- deterministic
- easy to debug
- fully controlled

---

## Finding unmapped exercises

select distinct e.title as exercise_name
from hevy_workout_exercises e
left join hevy_exercise_muscle_map m
  on e.title = m.exercise_name
where m.exercise_name is null
order by e.title;

---

## Adding new mappings

insert into hevy_exercise_muscle_map (exercise_name, primary_muscle_group)
values ('Squat (Barbell)', 'quads')
on conflict (exercise_name)
do update set primary_muscle_group = excluded.primary_muscle_group;

---

## Muscle group categories

Common values:
- quads
- hamstrings
- glutes
- calves
- chest
- back
- shoulders
- biceps
- triceps
- abs
- posterior_chain

---

## Fatigue score

Fatigue score is based on volume and intensity:

fatigue_score = (weight * reps) * (RPE / 10)

Used to:
- compare muscle stress
- track overload
- balance training

---

## Project usage

This service feeds:
- Claude MCP tools
- progression analysis
- fatigue analysis
- coaching logic

---

## Common issues

### Missing fatigue data
- exercise not mapped

### Empty views
- no workouts synced yet

### Duplicate names
- same lift with different naming variations

---

## Git safety

Do NOT commit:
- .env
- API keys

---

## Suggested folder structure

hevy/
├── .env.example
├── Dockerfile
├── hevy_sync.py
├── README.md
├── requirements.txt

---

## System role

Hevy provides:

muscular load + progression signal

Combined with:
- Garmin (recovery)
- Strava (endurance)
- Nutrition (fuel)

It completes the full training model.
