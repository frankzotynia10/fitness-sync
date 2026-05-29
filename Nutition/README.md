# Nutrition Integration (iOS Shortcuts)

This component handles daily nutrition tracking by extracting macros from a mobile nutrition app using iOS Shortcuts, then writing that data into Postgres.

Shortcut file provided in repo.  Import to iPhone.

---

## Overview

- iOS Shortcuts used to capture daily macros
- Data sent to backend (HTTP or script)
- Stored in Postgres (daily_nutrition table)

---

## Data captured

- date
- calories
- protein_g
- carbs_g
- fat_g

---

## Workflow

1. Log food in nutrition app
2. Run iOS Shortcut
3. Extract macros
4. Send JSON payload
5. Insert into database

---

## Example payload

{
  "date": "2026-05-29",
  "calories": 2450,
  "protein_g": 180,
  "carbs_g": 250,
  "fat_g": 70
}

---

## Database table

create table if not exists daily_nutrition (
  date date primary key,
  calories numeric,
  protein_g numeric,
  carbs_g numeric,
  fat_g numeric,
  updated_at timestamp default now()
);

---

## Integration

Used by:
- daily_training_nutrition_context view
- daily_underfueling_signals view

---

## Purpose

Provides fueling data for:
- recovery analysis
- training load comparison
- underfueling detection

---

## Git safety

Do NOT commit:
- .env
- API keys
