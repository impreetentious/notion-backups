# Storage Policy

Google Drive is the long-term archive layer. GitHub does not retain any committed backup snapshots in the current production model.

## GitHub Layer

- GitHub Actions creates a working snapshot during the run.
- The storage step finalizes the manifest with the archive outcome before the tar is built, and retains the finalized manifest for the notification step even after the uploaded snapshot is removed from the runner.
- Archives are always rebuilt from the finalized snapshot through a temporary file and atomic replace; a tar left behind by an earlier or interrupted attempt is never reused. Snapshot directories without a `manifest.json` are never archived and are reported as warnings.
- No backup snapshots are retained in the GitHub repository. After a successful Google Drive upload, the snapshot is removed from the runner.

If Google Drive archival is unavailable or fails, the run reports a storage warning instead of pretending the archive completed. The surviving local snapshot is only on the ephemeral Actions runner and is lost when the job ends, so that run has no durable backup.

## Archive Layer

Google Drive receives `tar.gz` archives in:

```text
notionbackups:NotionBackups/
```

The remote name `notionbackups` must exist in the base64-encoded `rclone.conf` stored in `RCLONE_CONFIG_B64`.

## Retention

Drive archives are retained for at least 30 days. The workflow deletes remote archives older than 30 days using `rclone deletefile`, but only after the current snapshot has been uploaded successfully; when an upload fails, cleanup is skipped so the remaining archives are preserved. Cleanup listing, parsing or deletion failures downgrade the storage status to `warning` so they surface in notifications.

If remote deletion is disabled, existing Drive archives are preserved. If Drive itself is not configured, the workflow reports a warning but cannot preserve a durable copy after the ephemeral Actions job ends.
