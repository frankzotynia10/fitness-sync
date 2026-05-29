# Supabase Self-Hosted (Docker Compose Guide)

This guide explains how to run **Supabase locally using Docker Compose** for use with the fitness-sync project.

Supabase provides:
- PostgreSQL database
- REST API (PostgREST)
- Authentication (GoTrue)
- Realtime subscriptions
- Storage (optional)

For this project, Supabase is primarily used as:

> a managed Postgres + API + auth layer

---

## Overview Architecture

```text
Docker
  └── Supabase stack
       ├── Postgres
       ├── Kong (API gateway)
       ├── GoTrue (Auth)
       ├── PostgREST
       ├── Realtime
       └── Studio (UI)
```

---

## Prerequisites

- Docker + Docker Compose installed
- Open ports: 5432, 54321, 8000 (optional overlap depending config)
- 4GB+ RAM recommended

---

## Step 1 – Clone Supabase repo

```bash
git clone https://github.com/supabase/supabase
cd supabase/docker
```

---

## Step 2 – Copy environment file

```bash
cp .env.example .env
```

Then update key values:

```env
POSTGRES_PASSWORD=your_secure_password
JWT_SECRET=your_jwt_secret
ANON_KEY=your_anon_key
SERVICE_ROLE_KEY=your_service_role_key
```

---

## Step 3 – Start Supabase

```bash
docker compose up -d
```

This starts all services:

- Postgres → port 5432
- Studio → http://localhost:54323
- API → http://localhost:8000

---

## Step 4 – Access Supabase Studio

Open:

```text
http://localhost:54323
```

You can:
- browse tables
- run SQL
- manage users

---

## Step 5 – Connect fitness-sync services

Update your `.env` files across services:

```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=your_secure_password
```

All services (Garmin, Strava, Hevy, MCP) should now point to Supabase Postgres.

---

## Optional – Create read-only user for Claude MCP

```sql
create user claude_reader with password 'secure_password';

grant connect on database postgres to claude_reader;

grant usage on schema public to claude_reader;

grant select on all tables in schema public to claude_reader;

grant select on all sequences in schema public to claude_reader;
```

Then use in MCP `.env`:

```env
DB_USER=claude_reader
```

---

## Optional – Disable unneeded services

If you only want Postgres:

Edit `docker-compose.yml` and disable:
- storage
- edge-runtime
- realtime (optional)

This reduces memory usage significantly.

---

## Common Ports

| Service        | Port |
|----------------|------|
| Postgres       | 5432 |
| API (Kong)     | 8000 |
| Studio UI      | 54323|

---

## Troubleshooting

### Port already in use

```bash
lsof -i :5432
```

Change port in docker-compose if needed.

---

### Services not starting

```bash
docker compose logs -f
```

---

### Reset environment

```bash
docker compose down -v
```

---

## Git safety

Do NOT commit:
- `.env`
- passwords
- JWT secrets

Do commit:
- `docker-compose.yml`
- setup instructions

---

## Role in the project

Supabase acts as:

> ✅ central data platform

It powers:
- all sync pipelines (Garmin, Strava, Hevy, Nutrition)
- analytics views
- Claude MCP queries

---

## Next steps

- create tables (`garmin_daily`, `strava_activities`, etc.)
- run sync services
- connect Claude MCP
- build analytics views

---

## Summary

With Supabase running locally, you now have:

✅ Postgres DB
✅ API layer (optional)
✅ Auth system (optional)
✅ UI for debugging

This is the foundation for the full fitness-sync system.
