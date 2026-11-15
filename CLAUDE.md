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
- garmin-sync source is `GARMIN_SOURCE` in the garmin `.env`: `official` = Garmin Personal API first (`GARMIN_PERSONAL_KEY`), garminconnect fills gaps (training metrics, some intraday, FTP, race predictions); `connect` = old behavior. Verify changes with `GARMIN_DRY_RUN=1` + `tools/compare_dry_runs.py` (see garmin/readme.md). Personal API limit: 100 req/window. Env changes need a container recreate, not just `docker exec`.
- Garmin auto-upload to Strava is ON; the pipeline relies on it. Strava's native Sets table can't be written or deleted via API; the description text is the ceiling.
- Strava webhook subscription 351462 triggers strava-sync on new activities.

## Dependencies
- Dependabot weekly (pip x4, actions). Weekly Claude scheduled task (Mondays 7:45am ET) reports open PRs; report-only.
- `garminconnect` is unofficial and auth-sensitive (0.3.x needs Python 3.12; base images are 3.12-slim). hevy-sync no longer depends on `fit-tool` (removed 2026-11-14; unused since the Jun 8 switch to Strava JSON upload). hevy2garmin has its own `fit-tool<0.9.16` pin upstream. `fastmcp` is on 4.x; verify the Fitness DB MCP connector after changes.
- Dependabot rebase: comment `@dependabot rebase` on conflicted PRs (the GitHub MCP token can merge but not comment or close).

## Ops gotchas
- Compose project name comes from the folder (`claude-mcp-001`). Don't add a conflicting `name:`; Compose Manager autostart fails if two project names own live resources.
- hevy2garmin uses bind mounts under `/mnt/user/appdata/hevy2garmin/{garmin_auth,data}` (appdata.backup can't back up named volumes). The container runs as uid 999 (`nonroot`); dirs must stay writable.
- Backups: appdata.backup runs at midnight in stop/backup/start mode.
- hevy2garmin env holds secrets (`.env` only). Rotate if ever pasted anywhere.

## Security setup (2026-11-14)
- **n8n webhooks:** `fitness-deploy` needs header `X-Deploy-Secret` (credential "Fitness Deploy Secret"; GitHub secret `N8N_DEPLOY_SECRET` in fitness-sync and hevy2garmin). `fitness-sync` (iOS sync) needs `X-Shortcut-Key` (credential "iOS shortcut password", stored in Frank's Passwords). `hevy-workout-saved` has its own header auth and rejects non-UUID workout IDs before SSH. `strava-webhook` can't carry a header, so it only accepts our subscription and athlete IDs (the 'Is Our Activity Create?' node; keep the IDs out of this public repo). LiveTrack Manual End and Zwift Launcher are archived (Zwift launches via HomeKit/Harmony). n8n edits are drafts until published; the editor tab blocks MCP publishes.
- **PostgREST (supabase-rest, :3001):** connects as `authenticator`; anonymous role `web_anon` (no rights). Health Data Sync sends `Authorization: Bearer <JWT role=nutrition_writer>` (token at `/mnt/user/appdata/supabase/nutrition_token.txt`, secret `PGRST_JWT_SECRET` in the supabase `.env`); `nutrition_writer` can only SELECT/INSERT/UPDATE `daily_nutrition`. `anon`/`authenticated`/`service_role` have no table rights in `public` (incl. default privileges).
- **Supabase ops:** every service shares `/mnt/user/appdata/supabase/.env`, so editing it and running `docker compose up -d <svc>` also recreates the DB. Always use `--no-deps`. `postgres` is not a superuser in this image; reserved roles (e.g. `authenticator`) need `psql -U supabase_admin` from inside the container.
- **pg_hba:** bind-mounted from `/mnt/user/appdata/supabase/pg_hba.conf` (private ranges + localhost only; image default in `pg_hba.conf.orig`). Postgres sees real client IPs, so address rules work.
- **garmin-sync:** `GARMIN_TZ=America/New_York` in the garmin `.env` (container clock is UTC).

## Garmin Personal API (done, live 2026-11-14)
Spike findings: `experiments/garmin-personal-api/FINDINGS.md`. Shipped in PR #50: garmin-sync runs `GARMIN_SOURCE=official` on Andromeda (Personal API first, garminconnect gap-fill; see garmin/readme.md).
- Overlap is identical to garminconnect (daily values, intraday HR/HRV/BB, activity summaries, FIT bytes). The API has no readiness, training status/load, race predictions or FTP (unlisted paths 404); watch FITs carry recovery time, VO2 max and activity load in msg 140 (not used yet).
- Token call: `Accept: */*`, returns a bare JWT as text/plain, ~24h, no refresh. Rate limit 100 req/window. Key `GARMIN_PERSONAL_KEY` in `/mnt/user/appdata/fitness-sync/garmin/.env`; expires (30/90/180/365 days), and `garmin_token_health.py` fails when it does.
- **Pull-only:** hevy2garmin and strava-sync still need the unofficial login.

## Backlog (someday)
FatSecret nutrition logger, shed WiFi AP for Garmin sync, cycling poller (re-evaluate with Garmin auto-upload dependency), Health Auto Export swap, h2g migration before PyPI package end date 2026-10-31 (fork stays on current line for now). Supabase Studio (:3000) has no login and is open on the LAN: bind it to 127.0.0.1 and use an SSH tunnel (left as is on 2026-11-14 by choice). hevy2garmin merge-timing fix only applies when the watch records each set.
