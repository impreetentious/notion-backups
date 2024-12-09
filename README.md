# Notion Backups

Private, automated cron-based backup system for exporting Notion snapshots.

## What It Does

- Runs automatically every Monday and Friday at `00:30 IST`.
- Keeps a manual GitHub Actions trigger.
- Reads from Notion only. It never creates, edits, moves, archives or deletes Notion content.
- Writes restore-oriented snapshots into `exports/`.
- Uploads snapshots directly to Google Drive as `tar.gz` archives when Drive is configured.
- Retains at least 30 days of external archives by default.
- Uses a hybrid restore format: Markdown for page content, JSON for raw Notion metadata/databases and `manifest.json` for restore mapping.
- Records status, size, format version, timestamp, storage destination, warnings and errors in each manifest.

## Required Secrets

Add these in GitHub:

`Settings -> Secrets and variables -> Actions -> New repository secret`

- `NOTION_TOKEN`: required. Use a Notion integration token with access only to the pages/databases you want backed up.
- `RCLONE_CONFIG_B64`: required for Google Drive archival. Base64-encoded `rclone.conf` containing a remote named `notionbackups`.
- `SMTP_HOST`: required for email notifications.
- `SMTP_PORT`: required for email notifications, usually `587`.
- `SMTP_USERNAME`: required for email notifications.
- `SMTP_PASSWORD`: required for email notifications.
- `NOTIFY_EMAIL_TO`: required for email notifications.
- `NOTIFY_EMAIL_FROM`: optional if the SMTP username can be used as the sender.
- `NOTIFY_WEBHOOK_URL`: optional. Used only if the webhook channel is enabled in `config/backup_config.json`.

Optional repository variable:

- `SMTP_USE_TLS`: optional, defaults to `true`.

## Schedule

The workflow cron is in `.github/workflows/notion-backup.yml`:

```yaml
- cron: "00 19 * * 0,4"
```

GitHub cron uses UTC. `19:00 UTC` on Sunday and Thursday is `00:30 IST` on Monday and Friday.

Use **Actions -> Notion Backup -> Run Workflow** for a manual run.

## Backup Flow

1. GitHub Actions starts on schedule or manual trigger.
2. `scripts/backup_notion.py` loads `config/backup_config.json`.
3. The runner backs up only the configured root pages.
4. The runner reads Notion pages/databases through read-oriented API calls.
5. A versioned snapshot is written under `exports/NB_YYYYMMDD_HHMMSS+0530/`.
6. The manifest records status, size, format version, timestamp, warnings/errors, restore map and storage metadata.
7. `scripts/manage_storage.py` updates the final manifest with archive status and sends snapshots to Google Drive.
8. GitHub Actions sends notifications after the final manifest has the completed size and storage details.

## Google Drive Setup

This repo uses `rclone` because it is stable in GitHub Actions and avoids custom Drive API code.

1. Install `rclone` locally.
2. Create a Google Drive remote named `notionbackups`.
3. Confirm `rclone lsd notionbackups:` works locally.
4. Base64 encode the config:

```bash
base64 -i ~/.config/rclone/rclone.conf
```

5. Add the resulting value as the GitHub secret `RCLONE_CONFIG_B64`.

Snapshots are uploaded to `notionbackups:NotionBackups/`. Remote archives older than 30 days are deleted automatically when rclone can list and delete them.

If Drive is not configured, the backup still runs, the local workflow snapshot remains unarchived, and the notification reports a storage warning.

## Backup Format

See `docs/FORMAT.md`.

Snapshot structure:

```text
exports/NB_YYYYMMDD_HHMMSS+0530/
  manifest.json
  pages/<page-id>/content.md
  pages/<page-id>/metadata.json
  pages/<page-id>/blocks.json
  databases/<database-id>/database.json
  databases/<database-id>/rows.json
```

`manifest.json` is the restore entry point. It records format version, run metadata, object counts, original Notion IDs, parent references, file paths, size, status, storage destination, linked-view references and any warnings/errors from recoverable traversal failures.

## Notifications

Every run writes a GitHub Actions step summary and attempts email notification.

Email contains:

- backup size
- backup version
- format version
- timestamp
- storage destination
- pass/fail or warning status
- warning and error counts/details

Webhook notifications are still available by enabling `generic_webhook` in `config/backup_config.json`.

## Operational Notes

Human intervention should only be needed for expired credentials, Notion API changes, quota/rate-limit issues or deliberate config changes.

- Secondary linked database view wrappers are recorded as `linked_database_view` manifest objects instead of warnings because they contain no unique row data and cannot be queried through Notion's public API. Genuine inaccessible child databases still surface as warnings. Fatal root resolution failures, root page metadata failures and config errors stop the run because there is no reliable root snapshot to commit.