# fitness-sync: project notes for Claude Code

Owner: Frank. Self-hosted fitness data pipeline. Be direct, favor the simplest working fix, minimize maintenance, no babysitting. Never put secrets in commits or chat.

## Infrastructure
- **Andromeda (10.10.0.10, Unraid):** runs all containers except NPM. Compose projects live in `/boot/config/plugins/compose.manager/projects/` (`fitness-sync`, `hevy2garmin`, `claude-mcp-001`, `supabase`).
- **Apollo (10.10.0.11):** nginx proxy manager (internet access) and n8n.
- **Repos:** `frankzotynia10/fitness-sync` (garmin-sync, hevy-sync, strava-sync, claude-mcp) and `frankzotynia10/hevy2garmin` (fork of drkostas/hevy2garmin).
- **DB:** Supabase Postgres (`supabase-db`, rest, meta, studio). hevy2garmin has its own DB `hevy2garmin`.

## CI/CD
- Push to `main` runs GitHub Actions, builds images to GHCR, then POSTs the n8n `fitness-deploy` webhook, which SSHes to Andromeda for `docker compose pull && up -d`, then runs sync commands.
- fitness-sync's workflow has **no path filtering**: every merge rebuilds all four images. Merge PRs one at a time.
- Push to GitHub instead of offering downloads. Frank does not build locally.

## Key flows
- Hevy webhook -> n8n -> hevy2garmin sync -> poll `synced_workouts` / `pending_uploads` -> `docker exec strava-sync python3 strava_replace_activity.py --hevy-id X --garmin-id Y` (updates Strava name/description in place).
- hevy2garmin runs in **Merge mode** (keeps watch metrics; exercises need `probability: 100` to show names on Garmin). Don't blindly reset the fork to upstream `main`: upstream 0.12.0 removed the Python dashboard.
- Garmin auto-upload to Strava is ON; the pipeline relies on it. Strava's native Sets table can't be written or deleted via API; the description text is the ceiling.
- Strava webhook subscription 351462 triggers strava-sync on new activities.

## Dependencies
- Dependabot weekly (pip x4, actions). Weekly Claude scheduled task (Mondays 7:45am ET) reports open PRs; report-only.
- `garminconnect` is unofficial and auth-sensitive (0.3.x needs Python 3.12; base images are 3.12-slim). `fit-tool` is pinned to 0.9.15 in hevy-sync because 0.9.16 regressed HR parsing. `fastmcp` is on 4.x; verify the Fitness DB MCP connector after changes.
- Dependabot rebase: comment `@dependabot rebase` on conflicted PRs (the GitHub MCP token can merge but not comment or close).

## Ops gotchas
- Compose project name comes from the folder (`claude-mcp-001`). Don't add a conflicting `name:`; Compose Manager autostart fails if two project names own live resources.
- hevy2garmin uses bind mounts under `/mnt/user/appdata/hevy2garmin/{garmin_auth,data}` (appdata.backup can't back up named volumes). The container runs as uid 999 (`nonroot`); dirs must stay writable.
- Backups: appdata.backup runs at midnight in stop/backup/start mode.
- hevy2garmin env holds secrets (`.env` only). Rotate if ever pasted anywhere.

## Spike done: Garmin Personal API (Oct 21, 2026 launch)
**Result (2026-11-14): see `experiments/garmin-personal-api/FINDINGS.md`.** Works; covers about two-thirds of `garmin_daily` plus activities and an unstripped original FIT. No readiness, training status/load, endurance, recovery, race predictions or FTP (unlisted paths 404). Pipeline unchanged. Key is `GARMIN_PERSONAL_KEY` in `/mnt/user/appdata/fitness-sync/garmin/.env`. The token call needs `Accept: */*` and returns a bare JWT as text/plain.
Original goal: find out what the new official, free, read-only API exposes versus what garmin-sync stores via unofficial `garminconnect`.
- Base `https://personal.connectapi.garmin.com`; POST the personal API key (`Authorization: ApiKey`) to `/personalapitoken-service/personal-api/personal-api-key/token` for a 24h access token (no refresh). Keys expire in 30/90/180/365 days. Personal use only.
- Endpoints: activities (list/search, detail, FIT download, workouts, range summary), daily summaries v1/v2, sleep, HRV, body battery daily report, stress, steps, hydration, heart rate, SpO2, weight, HR zones, fitness age, nutrition logs, user settings.
- Not listed as endpoints: VO2 max, training load/status/readiness, recovery time, Training Effect, FTP, power curve. Garmin reportedly strips undocumented fields from API files, so check whether the activity DTO or FIT carries them.
- **Pull-only:** hevy2garmin still needs the unofficial login to write activities and sets to Garmin. Best case the API replaces only garmin-sync's reads.
- Plan: Frank generates a key at `connect.garmin.com/app/settings/personalApiToken` and puts `GARMIN_PERSONAL_API_KEY` in a local `.env` (never committed). Build a script under `experiments/garmin-personal-api/` that fetches a recent activity, its FIT file, and one day of summary, sleep, HRV and body battery, then diffs fields against what garmin-sync stores. Time-boxed spike; output is a short findings note, not production code. Don't touch the running pipeline until the findings say it's worth it.

## Backlog (someday)
FatSecret nutrition logger, shed WiFi AP for Garmin sync, cycling poller (re-evaluate with Garmin auto-upload dependency), Health Auto Export swap, h2g migration before PyPI package end date 2026-10-31 (fork stays on current line for now).
