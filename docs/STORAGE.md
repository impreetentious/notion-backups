# Storage Policy

Google Drive is the long-term archive layer. GitHub does not retain any committed backup snapshots in the current production model.

## GitHub Layer

- GitHub Actions creates a working snapshot during the run.
- The manifest is updated with final archive status before success notifications are sent.
- `storage.github.keep_latest_snapshots: 0` means no backup snapshots should remain committed to the repository after archival.

If Google Drive archival is unavailable, the run reports a storage warning instead of pretending the archive completed.

## Archive Layer

Google Drive receives `tar.gz` archives in:

```text
notionbackups:NotionBackups/
```

The remote name `notionbackups` must exist in the base64-encoded `rclone.conf` stored in `RCLONE_CONFIG_B64`.

## Retention

Drive archives are retained for at least 30 days. The workflow attempts to delete remote archives older than 30 days using `rclone deletefile`.

If Drive or deletion is not configured, the workflow preserves data and reports a storage warning rather than deleting data blindly.
