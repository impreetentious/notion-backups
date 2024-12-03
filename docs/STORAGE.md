# Storage Policy

GitHub is the active backup layer. Google Drive is the archive layer.

## Active Layer

The repository keeps:

- latest backup (`T`)
- previous backup (`T-1`)

Older expanded snapshot directories are removed from GitHub only after their archive uploads to Google Drive successfully.

## Archive Layer

Google Drive receives `tar.gz` archives in:

```text
notionbackups:NotionBackups/
```

The remote name `notionbackups` must exist in the base64-encoded `rclone.conf` stored in `RCLONE_CONFIG_B64`.

## Retention

Drive archives are retained for at least 60 days. The workflow attempts to delete remote archives older than 60 days using `rclone deletefile`.

If Drive or deletion is not configured, the workflow preserves data and reports a storage warning rather than deleting local copies blindly.
