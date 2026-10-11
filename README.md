# Notion Backups

Personal, automated cron-based backup system for exporting Notion snapshots.

## What It Does

- Is scheduled by GitHub Actions every Monday and Friday for `00:30 IST`; GitHub may start scheduled jobs later under load.
- Keeps a manual GitHub Actions trigger.
- Reads from Notion only. It never creates, edits, moves, archives or deletes Notion content.
- Uses Notion API `2026-03-11`: database IDs identify containers, and each accessible data source is backed up with its own schema and rows, including multi-source databases.
- Paces outbound Notion API calls with a client-side limiter (default: 3 requests/sec, burst of 8) so the run stays under Notion's rate limits proactively, in addition to retrying rate-limited/server-error responses with backoff.
- Fetches complete title, rich-text, people and relation property values through Notion's paginated property-item endpoint, instead of stopping at the 25 references Notion returns inside page objects.
- Writes restore-oriented snapshots into `exports/`.
- Uploads snapshots directly to Google Drive as `tar.gz` archives when Drive is configured.
- Retains at least 30 days of external archives by default.
- Uses a hybrid restore format: Markdown for page content, JSON for raw Notion metadata/databases and `manifest.json` for restore mapping.
- Records status, size, format version, timestamp, storage destination, warnings and errors in each manifest.

## Requirements

- Python 3.12 or newer — the backup logic is pure standard library, with no packages to install. CI and the backup workflow both run 3.12.
- The `rclone` binary, only if you want Google Drive archival.

## Choosing What Gets Backed Up

`config/backup_config.json` decides the scope. The runner backs up only the roots listed there, plus everything reachable beneath them:

```json
{
  "backup": {
    "scope": { "mode": "configured_roots" },
    "roots": [
      {
        "type": "page",
        "id": "<notion-page-id>",
        "title": "Expected page title",
        "top_level_only": true,
        "enabled": true
      }
    ]
  }
}
```

- `id` is the Notion page or database ID. Your integration must have been shared into that page, or the run cannot see it.
- `enabled: false` keeps a root in the file but skips it.
- `title` is not a cosmetic label. When a page root has both `id` and `title`, the run verifies that the live Notion title still matches and **aborts the whole backup** on a mismatch, so a silently re-pointed ID cannot be backed up as if it were the intended page. Renaming a root page in Notion therefore requires updating this file — see [Operational Notes](#operational-notes). A page root may instead give `title` alone, in which case the run resolves it by searching Notion and fails if the title is not unique.
- `top_level_only` asserts that the root is a workspace-level page: the run fails if the page turns out to be nested under another page, and title-only resolution ignores non-workspace matches. It does **not** limit traversal depth — every root is crawled through its full subtree of child pages, databases and rows regardless of this flag.

Neither flag restricts scope; scope is exactly the listed roots plus everything reachable beneath them.

`scope.mode` has one alternative, `all_top_level_pages`, which ignores the roots list and instead backs up every workspace-level page the integration can see. It cannot be combined with enabled roots — that combination is rejected at config load rather than silently resolved.

The same file also holds the API pacing settings, the timezone, the retention window, the Drive remote, and which notification channels are active.

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
  databases/<database-id>/data_sources/<data-source-id>/data_source.json
  databases/<database-id>/data_sources/<data-source-id>/rows.json
```

`manifest.json` is the restore entry point. It records format version, run metadata, object counts, original Notion IDs, parent references, file paths, size, status, storage destination, linked-view references and any warnings/errors from recoverable traversal failures. Database containers and their individual data sources are separate manifest objects, so each source keeps its own schema and row path.

`size_bytes`/`size_human` measure the snapshot payload excluding `manifest.json` itself, at both the initial snapshot write and the storage finalize step, so the reported size is stable regardless of when the manifest was last rewritten.

## Restoring

There is no automated restore tool. Restoring is a manual process driven by `manifest.json`, which maps every exported file back to the Notion object it came from, including parent references so a tree can be rebuilt in order. Page content is Markdown; database schemas and rows are raw Notion JSON.

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

Run the unit tests from the repository root with the package sources on `PYTHONPATH`:

```bash
PYTHONPATH=scripts python3 -m unittest discover -s tests
```

The same suite runs in CI on pushes to `main`, on every pull request, and on manual dispatch via `.github/workflows/tests.yml`.

## Operational Notes

Human intervention may be needed for expired credentials, Notion API changes, quota/rate-limit issues, deliberate config changes, or a missed/disabled schedule.

Renaming a configured root page in Notion breaks every subsequent run until `config/backup_config.json` is updated: the title check is fatal by design, so the run stops instead of silently backing up the wrong page. The same applies to moving a `top_level_only` root underneath another page.

GitHub automatically disables scheduled workflows in **public** repositories after 60 days without repository activity, and an in-workflow notification cannot report a job that never starts. If this repository is public, check the Actions schedule periodically; a private repository is not subject to that auto-disable rule.

Secondary linked database view wrappers are recorded as `linked_database_view` manifest objects instead of warnings because they contain no unique row data and cannot be queried through Notion's public API. Genuine inaccessible child databases still surface as warnings. Fatal root resolution failures, root page metadata failures and config errors stop the run because there is no reliable root snapshot to commit.

## Project Status

This is a personal backup system that is published openly so it can be read, forked and adapted; it is not a general-purpose product and there is no support commitment. It is in active use against the workspace described by `config/backup_config.json`. To run your own copy, fork the repository, replace the roots in that file with your own Notion IDs, and add the secrets listed above.

Issues and pull requests are welcome but may not be answered quickly. Please keep changes focused and make sure `PYTHONPATH=scripts python3 -m unittest discover -s tests` passes.

## License

MIT © Sidakpreet Singh — see [LICENSE](LICENSE).

---

**Version:** v1.4.2
