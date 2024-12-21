# Notion Backups

Personal, automated cron-based backup system for exporting Notion snapshots.

**Release version:** `v1.4.0`

## What It Does

- Is scheduled by GitHub Actions every Monday and Friday for `00:30 IST`; GitHub may start scheduled jobs later under load.
- Keeps a manual GitHub Actions trigger.
- Reads from Notion only. It never creates, edits, moves, archives or deletes Notion content.
- Paces outbound Notion API calls with a client-side limiter (default: 3 requests/sec, burst of 8) so the run stays under Notion's rate limits proactively, in addition to retrying rate-limited/server-error responses with backoff.
- Fetches complete title, rich-text, people and relation property values through Notion's paginated property-item endpoint, instead of stopping at the 25 references Notion returns inside page objects.
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

GitHub cron uses UTC. `19:00 UTC` on Sunday and Thursday is the nominal `00:30 IST` schedule on Monday and Friday.

Use **Actions -> Notion Backup -> Run Workflow** for a manual run.

## Backup Flow

1. GitHub Actions starts on schedule or manual trigger.
2. `scripts/backup_notion.py` loads `config/backup_config.json`.
3. The runner backs up only the configured root pages.
4. The runner reads Notion pages/databases through read-oriented API calls.
5. A versioned snapshot is written under `exports/NB_YYYYMMDD_HHMMSS+0530/`.
6. The manifest records status, size, format version, timestamp, warnings/errors, restore map and storage metadata.
7. `scripts/manage_storage.py` updates the final manifest with archive status and sends snapshots to Google Drive. This runs whether the upload succeeds or fails, so a failed Drive upload is always reflected in the manifest instead of silently keeping a stale "pending" status.
8. GitHub Actions runs notifications after storage handling. The finalized manifest is retained for the notification step even after a successfully uploaded snapshot is removed from the runner, so success emails and summaries carry the real size, format and storage metadata.

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

Snapshots are uploaded to `notionbackups:NotionBackups/`. Remote archives older than 30 days are deleted automatically after the current snapshot has been uploaded successfully; if the upload fails, cleanup is skipped so the existing archives are preserved.

If Drive is not configured, the backup still runs and the notification reports a storage warning, but the snapshot exists only on the ephemeral Actions runner and is discarded when the job ends — no durable copy is kept. The same is true if Drive is configured but the upload itself fails (expired credentials, misconfigured remote, etc.) — both the success and failure notification steps read the storage summary, so a failed archival is never reported as a clean run.

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

`size_bytes`/`size_human` measure the snapshot payload excluding `manifest.json` itself, at both the initial snapshot write and the storage finalize step, so the reported size is stable regardless of when the manifest was last rewritten.

## Notifications

When the workflow reaches the notification step, it writes a GitHub Actions step summary and attempts email. An enabled channel whose required settings are missing fails the notification step instead of passing silently, so a misconfigured channel is visible in the workflow run.

Email contains:

- backup size
- backup version
- format version
- timestamp
- storage destination
- pass/fail or warning status
- warning and error counts/details

Webhook notifications are still available by enabling `generic_webhook` in `config/backup_config.json`.

## Development

The backup logic is pure-stdlib Python; the only external binary is `rclone`. Run the unit tests from the repository root with the package sources on `PYTHONPATH`:

```bash
PYTHONPATH=scripts python3 -m unittest discover -s tests
```

The same suite runs in CI on pushes to `main` and `nb-branch`, on every pull request, and on manual dispatch via `.github/workflows/tests.yml`.

## Operational Notes

Human intervention may be needed for expired credentials, Notion API changes, quota/rate-limit issues, deliberate config changes, or a missed/disabled schedule.

The repository is currently public. GitHub automatically disables scheduled workflows in public repositories after 60 days without repository activity, and an in-workflow notification cannot report a job that never starts. Making the repository private removes that auto-disable risk; until then, keep an eye on the Actions schedule.

- Secondary linked database view wrappers are recorded as `linked_database_view` manifest objects instead of warnings because they contain no unique row data and cannot be queried through Notion's public API. Genuine inaccessible child databases still surface as warnings. Fatal root resolution failures, root page metadata failures and config errors stop the run because there is no reliable root snapshot to commit.
