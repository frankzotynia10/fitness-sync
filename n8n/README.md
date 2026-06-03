# n8n Workflows

Backup of n8n workflow configurations. Since n8n git push requires an enterprise license, these are maintained manually.

## Workflows

| File | Name | Description |
|------|------|-------------|
| `container-watchdog.json` | Container Watchdog | Hourly health check on 9 containers. Restarts + alerts if still down. |
| `container-sync-schedules.json` | Container Sync Schedules | Scheduled syncs: Garmin/Strava hourly, Hevy 6h, hevy2garmin 30min + FIT replace. |
| `ios-shortcuts-fitness-sync-trigger.json` | iOS Shortcuts Fitness Sync Trigger | Webhook trigger from iOS Shortcut. Runs full sync chain. |
| `fitness-stack-deploy.json` | Fitness Stack Deploy | GitHub Actions webhook → deploy + sync. |
| `sync-staleness-watchdog.json` | Sync Staleness Watchdog | Every 6h staleness check on all data sources. |
| `postgres-weekly-backup.json` | Postgres Weekly Backup | Sunday 23:30 pg_dump, 30 day retention. |
| `weekly-training-digest.json` | Weekly Training Digest | Sunday 6:30PM training summary via ntfy. |
| `weekly-pr-notification.json` | Weekly PR Notification | Monday 8AM 1RM PR check via ntfy. |

## Restore

To restore a workflow: open n8n → Import from file → select the JSON.

## Notes
- Credentials are not stored here (SSH, ntfy, Postgres) — reconfigure manually after restore.
- Workflow IDs are preserved in each file for reference.
