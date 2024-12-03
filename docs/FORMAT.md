# Backup Format

Format name: `notion-hybrid-backup`

Current version: `1.2.0`

This repository treats the backup format as a stable restore contract. Future incompatible changes should create a new version and keep readers for old versions.

## Snapshot Layout

Each run writes one versioned snapshot:

```text
exports/NB_YYYYMMDD_HHMMSS+0530/
  manifest.json
  pages/
    <notion-page-id-without-dashes>/
      content.md
      metadata.json
      blocks.json
  databases/
    <notion-database-id-without-dashes>/
      database.json
      rows.json
  linked_views/
    <notion-block-id-without-dashes>/
      view_reference.json
```

Older snapshots are uploaded to Google Drive as:

```text
NotionBackups/NB_YYYYMMDD_HHMMSS+0530.tar.gz
```

## Manifest Metadata

`manifest.json` is the restore entry point and includes:

- `format.name` and `format.version`
- `format_version`
- `run_id` and `version`, using `NB_YYYYMMDD_HHMMSS+ZZZZ`
- `created_at` and `timestamp`
- `status`: `success`, `warning`, or `partial`
- `size_bytes` and `size_human`
- `storage.destination` and storage warnings/errors
- `roots`
- `counts`
- `warnings`
- `errors`
- `objects`
- `restore_map`

`status` is `warning` when recoverable traversal issues occurred and `partial` when an object had to be skipped as an error. Linked database view wrappers do not change `status` by themselves.

## Restore Contract

- `manifest.json` is the restore entry point.
- `manifest.json.restore_map` maps original Notion IDs to file paths and parent metadata.
- `manifest.json.complete` is `false` when recoverable traversal warnings or object errors occurred.
- `counts.linked_views` records secondary linked database view wrappers that Notion exposes as `child_database` blocks but does not allow the API to query directly.
- `linked_database_view` objects preserve wrapper metadata and a small `view_reference.json` artifact for restore/manual reconstruction reference.
- Page Markdown is for human-readable recovery and migration.
- Page/database JSON is the authoritative restore source because it preserves the raw API objects.
- Block JSON preserves unsupported Markdown block details.
- Paths are based on Notion IDs, not page titles, so renames do not create ambiguous file paths.

## Preserved Data

The backup preserves, as far as the Notion API exposes it:

- page IDs, database IDs, and block IDs
- page titles and properties
- database schemas and row/page objects
- parent references
- child page and child database links
- created and last edited timestamps
- users returned by Notion metadata
- relations and rollups returned inside page properties
- raw block data for blocks that are not perfectly represented in Markdown

## Partial Traversal Handling

If Notion repeatedly times out while listing a block's children, the parent page is still backed up. The affected block gets empty `children` in `blocks.json`, and `manifest.json.warnings` identifies the missing block subtree.

If Notion reports the well-known "does not contain any data sources accessible by this API bot" validation error for a `child_database` block, the block is recorded as a `linked_database_view` object instead of a warning. This is the API shape Notion uses for secondary linked database views with no unique row data.

If a child database is genuinely inaccessible for some other reason, the parent page still backs up and the skipped database is recorded as a warning.

Fatal root resolution failures, root page metadata failures, and config errors stop the run.

## Not Preserved

Notion API limitations apply. File/image URLs may expire if Notion returns signed URLs. The current backup records those URLs and metadata but does not download media binaries by default.
