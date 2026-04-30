from __future__ import annotations

import json
import logging
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import FORMAT_NAME, FORMAT_VERSION
from .config import enabled_roots, load_config
from .markdown import blocks_to_markdown, page_title
from .notion_client import DEFAULT_NOTION_VERSION, NotionApiError, NotionClient, normalize_id
from .retention import apply_retention
from .writer import create_tar_gz, directory_size_bytes, format_bytes, write_json, write_text

LOGGER = logging.getLogger(__name__)

# Notion truncates these list-valued page properties to 25 references in Page
# objects; complete values need the paginated property-item endpoint.
_PAGINATED_PROPERTY_TYPES = ("title", "rich_text", "relation", "people")
_PROPERTY_REF_LIMIT = 25

@dataclass(frozen=True)
class QueueItem:
    object_type: str
    object_id: str
    source: str
    title: str = ""
    parent: dict[str, Any] | None = None
    owner_id: str | None = None
    root_title: str = ""

class BackupRunner:
    def __init__(self, config: dict[str, Any], client: NotionClient) -> None:
        self.config = config
        self.client = client
        self.seen_pages: set[str] = set()
        self.seen_databases: set[str] = set()
        self.preloaded_pages: dict[str, dict[str, Any]] = {}
        self.manifest_objects: list[dict[str, Any]] = []
        self.restore_map: dict[str, dict[str, Any]] = {}
        self.warnings: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self._warning_keys: set[tuple[str, str, str]] = set()
        self.counts = {
            "pages": 0,
            "databases": 0,
            "data_sources": 0,
            "database_rows": 0,
            "blocks": 0,
            "block_fetch_errors": 0,
            "object_errors": 0,
            "linked_views": 0,
            "skipped_child_databases": 0,
        }
        self.counts_by_root: dict[str, dict[str, int]] = {}

    def run(self) -> Path:
        backup_config = self.config.get("backup", {})
        timezone_name = backup_config.get("timezone", "Asia/Kolkata")
        now = datetime.now(ZoneInfo(timezone_name))
        run_id = now.strftime("NB_%Y%m%d_%H%M%S%z")
        output_dir = Path(backup_config.get("output_dir", "exports"))
        archive_dir = Path(backup_config.get("archive_dir", "archives"))
        snapshot_dir = output_dir / run_id
        snapshot_dir.mkdir(parents=True, exist_ok=False)

        LOGGER.info("Starting Notion backup %s", run_id)
        queue = deque(self._seed_queue())

        while queue:
            item = queue.popleft()
            normalized_id = normalize_id(item.object_id)
            try:
                if item.object_type == "page":
                    if normalized_id in self.seen_pages:
                        continue
                    self.seen_pages.add(normalized_id)
                    queue.extend(self._backup_page(snapshot_dir, item))
                elif item.object_type == "database":
                    if normalized_id in self.seen_databases:
                        continue
                    self.seen_databases.add(normalized_id)
                    queue.extend(self._backup_database(snapshot_dir, item))
                else:
                    raise ValueError(f"Unsupported queue item type: {item.object_type}")
            except NotionApiError as exc:
                if self._record_linked_view(snapshot_dir, item, exc):
                    continue
                if self._should_warn_for_inaccessible_child_database(item, exc):
                    self.counts["skipped_child_databases"] += 1
                    self._record_warning(
                        "child_database_inaccessible",
                        item.object_id,
                        f"Child database skipped because Notion did not expose an accessible data source: {exc}",
                        {"type": item.object_type, "source": item.source},
                    )
                    LOGGER.warning("Skipping inaccessible child database %s", item.object_id)
                    continue
                if self._should_continue_after_object_error(item):
                    self.counts["object_errors"] += 1
                    self._record_error(
                        "object_backup_failed",
                        item.object_id,
                        f"{item.object_type} backup failed after retries: {exc}",
                        {"type": item.object_type, "source": item.source},
                    )
                    LOGGER.exception("Skipping %s %s after backup failure", item.object_type, item.object_id)
                    continue
                raise

        manifest = {
            "format": {"name": FORMAT_NAME, "version": FORMAT_VERSION},
            "format_version": FORMAT_VERSION,
            "created_at": now.isoformat(),
            "timestamp": now.isoformat(),
            "run_id": run_id,
            "version": run_id,
            "notion_api_version": self.client.notion_version,
            "roots": enabled_roots(self.config),
            "counts": self.counts,
            "warnings": self.warnings,
            "errors": self.errors,
            "complete": not self.errors and not self.warnings,
            "status": _status(self.warnings, self.errors),
            "size_bytes": 0,
            "size_human": "0 B",
            # Pre-storage-step scaffold. manage_storage.py overwrites this with the
            # final upload/delete results; the flat schema mirrors that shape so the
            # key is consistent even if the storage step is skipped.
            "storage": {
                "destination": "github",
                "status": "pending",
                "uploaded_to_external": [],
                "deleted_from_github": [],
                "warnings": [],
                "errors": [],
            },
            "objects": sorted(self.manifest_objects, key=lambda item: (item["type"], item["id"])),
            "restore_map": dict(sorted(self.restore_map.items())),
            "counts_by_root": dict(sorted(self.counts_by_root.items())),
        }
        _write_manifest_with_size(snapshot_dir, manifest)

        if self.config.get("compression", {}).get("enabled", False):
            create_tar_gz(snapshot_dir, archive_dir / f"{run_id}.tar.gz")

        if _runner_retention_enabled(self.config):
            retention = self.config.get("retention", {})
            deleted = apply_retention(
                output_dir=output_dir,
                archive_dir=archive_dir,
                retain_days=int(retention.get("retain_days", 30)),
                min_snapshots=int(retention.get("min_snapshots", 2)),
            )
            LOGGER.info("Retention removed %d expired paths", len(deleted))

        LOGGER.info(
            "Backup complete: pages=%d databases=%d rows=%d blocks=%d snapshot=%s "
            "notion_requests=%d pacing_wait_s=%.1f retry_wait_s=%.1f",
            self.counts["pages"],
            self.counts["databases"],
            self.counts["database_rows"],
            self.counts["blocks"],
            snapshot_dir,
            getattr(self.client, "total_requests", 0),
            getattr(self.client, "total_rate_limit_wait_seconds", 0.0),
            getattr(self.client, "total_retry_wait_seconds", 0.0),
        )
        for root_title in sorted(self.counts_by_root, key=lambda title: -self.counts_by_root[title]["blocks"]):
            bucket = self.counts_by_root[root_title]
            LOGGER.info(
                "  by root: %s -- pages=%d databases=%d rows=%d blocks=%d",
                root_title,
                bucket["pages"],
                bucket["databases"],
                bucket["database_rows"],
                bucket["blocks"],
            )
        return snapshot_dir

    def _seed_queue(self) -> list[QueueItem]:
        backup = self.config.get("backup", {})
        scope = backup.get("scope", {})
        mode = scope.get("mode", "configured_roots")
        roots = enabled_roots(self.config)
        # The scope mode is authoritative; config validation rejects the
        # ambiguous combination of all_top_level_pages plus enabled roots.
        if mode == "all_top_level_pages":
            return self._discover_top_level_pages()

        if roots:
            return [self._root_to_queue_item(root) for root in roots]

        if not backup.get("include_all_accessible", False):
            raise ValueError("No backup roots configured and include_all_accessible is false")

        LOGGER.info("No roots configured; searching all Notion objects accessible to the integration")
        seeds: list[QueueItem] = []
        for result in self.client.search_all():
            object_type = result.get("object")
            object_id = result.get("id")
            if object_type == "page" and object_id:
                seeds.append(QueueItem(object_type, object_id, "notion_search"))
            elif object_type == "data_source" and object_id:
                database_id = _data_source_parent_database_id(result)
                if not database_id:
                    data_source = self.client.retrieve_data_source(object_id)
                    database_id = _data_source_parent_database_id(data_source)
                if not database_id:
                    raise NotionApiError(
                        f"Search result data source {object_id} did not identify its parent database"
                    )
                seeds.append(QueueItem("database", database_id, "notion_search"))
        seeds.sort(key=lambda item: (item.object_type, normalize_id(item.object_id)))
        deduplicated: list[QueueItem] = []
        seed_keys: set[tuple[str, str]] = set()
        for seed in seeds:
            key = (seed.object_type, normalize_id(seed.object_id))
            if key not in seed_keys:
                seed_keys.add(key)
                deduplicated.append(seed)
        seeds = deduplicated
        LOGGER.info("Found %d accessible Notion seed objects", len(seeds))
        return seeds

    def _root_to_queue_item(self, root: dict[str, Any]) -> QueueItem:
        root_type = root["type"]
        root_id = root.get("id")
        if not root_id and root.get("id_env"):
            root_id = os.getenv(root["id_env"])

        if root_id:
            if root_type == "page" and (root.get("title") or root.get("top_level_only")):
                page = self.client.retrieve_page(root_id)
                if root.get("title") and page_title(page) != root["title"]:
                    raise ValueError(
                        f"Configured page root {root_id} title mismatch: "
                        f"expected {root['title']!r}, got {page_title(page)!r}"
                    )
                if root.get("top_level_only") and page.get("parent", {}).get("type") != "workspace":
                    raise ValueError(f"Configured page root {root_id} is not a top-level workspace page")
                self.preloaded_pages[normalize_id(root_id)] = page
            return QueueItem(root_type, root_id, "config_root", root_title=root.get("title", ""))

        if root_type == "page" and root.get("title"):
            page = self._resolve_page_by_title(root)
            page_id = page["id"]
            self.preloaded_pages[normalize_id(page_id)] = page
            return QueueItem("page", page_id, "resolved_config_root", root_title=root.get("title", ""))

        raise ValueError(f"Unable to resolve configured backup root: {root}")

    def _resolve_page_by_title(self, root: dict[str, Any]) -> dict[str, Any]:
        title = root["title"]
        top_level_only = root.get("top_level_only", False)
        page_size = int(self.config.get("notion", {}).get("root_search_page_size", 25))
        LOGGER.info("Resolving configured Notion page root by title: %s", title)
        matches: list[dict[str, Any]] = []
        for candidate in self.client.search_pages(title, page_size=page_size):
            candidate_id = candidate.get("id")
            if not candidate_id:
                continue
            page = self.client.retrieve_page(candidate_id)
            if page_title(page) != title:
                continue
            if top_level_only and page.get("parent", {}).get("type") != "workspace":
                continue
            matches.append(page)

        if not matches:
            scope_text = " top-level" if top_level_only else ""
            raise ValueError(f"No exact{scope_text} Notion page match found for title {title!r}.")
        if len(matches) > 1:
            ids = ", ".join(page["id"] for page in matches)
            raise ValueError(f"Multiple exact Notion page matches found for title {title!r}: {ids}.")
        return matches[0]

    def _discover_top_level_pages(self) -> list[QueueItem]:
        LOGGER.info("Discovering all top-level Notion pages accessible to the integration")
        roots: list[QueueItem] = []
        for candidate in self.client.search_pages("", page_size=int(self.config.get("notion", {}).get("page_size", 100))):
            candidate_id = candidate.get("id")
            if not candidate_id:
                continue
            page = self.client.retrieve_page(candidate_id)
            if page.get("parent", {}).get("type") != "workspace":
                continue
            self.preloaded_pages[normalize_id(candidate_id)] = page
            roots.append(QueueItem("page", candidate_id, "top_level_discovery", root_title=page_title(page)))
        roots.sort(key=lambda item: normalize_id(item.object_id))
        LOGGER.info("Discovered %d top-level Notion page roots", len(roots))
        return roots

    def _backup_page(self, snapshot_dir: Path, item: QueueItem) -> list[QueueItem]:
        page = self.preloaded_pages.pop(normalize_id(item.object_id), None) or self.client.retrieve_page(item.object_id)
        self._hydrate_page_properties(page)
        page_id = page["id"]
        page_dir = snapshot_dir / "pages" / normalize_id(page_id)
        blocks = self._fetch_block_tree(page_id)
        title = page_title(page)
        # json.dumps produces a double-quoted scalar that is also valid YAML, so
        # titles containing ":", quotes, or newlines cannot break the front matter.
        markdown = (
            f"---\nnotion_id: {page_id}\ntitle: {json.dumps(title, ensure_ascii=False)}\n"
            f"type: page\nformat_version: {FORMAT_VERSION}\n---\n\n"
        )
        markdown += blocks_to_markdown(blocks)

        write_json(page_dir / "metadata.json", page)
        write_json(page_dir / "blocks.json", blocks)
        write_text(page_dir / "content.md", markdown)

        block_count = _count_blocks(blocks)
        self.counts["pages"] += 1
        self.counts["blocks"] += block_count
        self._bump_root_count(item.root_title, "pages")
        self._bump_root_count(item.root_title, "blocks", block_count)
        self._record_object(
            object_id=page_id,
            object_type="page",
            source=item.source,
            parent=page.get("parent", {}),
            files={
                "markdown": _relative(snapshot_dir, page_dir / "content.md"),
                "metadata": _relative(snapshot_dir, page_dir / "metadata.json"),
                "blocks": _relative(snapshot_dir, page_dir / "blocks.json"),
            },
            title=title,
        )
        return _queue_children(blocks, owner_id=page_id, root_title=item.root_title)

    def _backup_database(self, snapshot_dir: Path, item: QueueItem) -> list[QueueItem]:
        database = self.client.retrieve_database(item.object_id)
        database_id = database["id"]
        database_dir = snapshot_dir / "databases" / normalize_id(database_id)

        data_sources = database.get("data_sources")
        if not isinstance(data_sources, list):
            raise NotionApiError(
                f"Database {database_id} did not return a data_sources list under API {self.client.notion_version}"
            )
        if not data_sources:
            raise NotionApiError(
                f"Database {database_id} does not contain any data sources accessible by this API bot.",
                status_code=400,
                body={
                    "object": "error",
                    "status": 400,
                    "code": "validation_error",
                    "message": "Database does not contain any data sources accessible by this API bot.",
                },
            )

        write_json(database_dir / "database.json", database)

        self.counts["databases"] += 1
        self._bump_root_count(item.root_title, "databases")
        self._record_object(
            object_id=database_id,
            object_type="database",
            source=item.source,
            parent=database.get("parent", {}),
            files={
                "metadata": _relative(snapshot_dir, database_dir / "database.json"),
            },
            title=_database_title(database),
            extra={"data_source_ids": [descriptor.get("id") for descriptor in data_sources if descriptor.get("id")]},
        )

        children: list[QueueItem] = []
        for descriptor in data_sources:
            if not isinstance(descriptor, dict) or not isinstance(descriptor.get("id"), str):
                raise NotionApiError(f"Database {database_id} returned an invalid data-source descriptor")
            children.extend(
                self._backup_data_source(
                    snapshot_dir, database_id, database_dir, descriptor["id"], item
                )
            )
        return children

    def _backup_data_source(
        self,
        snapshot_dir: Path,
        database_id: str,
        database_dir: Path,
        data_source_id: str,
        database_item: QueueItem,
    ) -> list[QueueItem]:
        data_source = self.client.retrieve_data_source(data_source_id)
        if data_source.get("id") != data_source_id:
            raise NotionApiError(
                f"Data source lookup for {data_source_id} returned unexpected ID {data_source.get('id')!r}"
            )
        if _data_source_parent_database_id(data_source) != database_id:
            raise NotionApiError(
                f"Data source {data_source_id} does not belong to database {database_id}"
            )

        rows = self.client.query_data_source(data_source_id)
        page_rows: list[dict[str, Any]] = []
        children: list[QueueItem] = []
        for row in rows:
            if not isinstance(row, dict):
                raise NotionApiError(f"Data source {data_source_id} query returned a non-object result")
            object_type = row.get("object")
            if object_type == "page":
                self._hydrate_page_properties(row)
                page_rows.append(row)
                if row.get("id"):
                    children.append(
                        QueueItem(
                            "page",
                            row["id"],
                            f"data_source:{database_id}:{data_source_id}",
                            root_title=database_item.root_title,
                        )
                    )
            elif object_type == "data_source":
                child_data_source_id = row.get("id")
                if not isinstance(child_data_source_id, str) or not child_data_source_id:
                    raise NotionApiError(
                        f"Data source {data_source_id} query returned a child data source without an ID"
                    )
                child_database_id = _data_source_parent_database_id(row)
                if not child_database_id:
                    child_data_source = self.client.retrieve_data_source(child_data_source_id)
                    child_database_id = _data_source_parent_database_id(child_data_source)
                if not child_database_id:
                    raise NotionApiError(
                        f"Nested data source {row.get('id')!r} did not identify its parent database"
                    )
                children.append(
                    QueueItem(
                        "database",
                        child_database_id,
                        f"data_source:{database_id}:{data_source_id}",
                        root_title=database_item.root_title,
                    )
                )
            else:
                raise NotionApiError(
                    f"Data source {data_source_id} query returned unsupported object type {object_type!r}"
                )

        data_source_dir = database_dir / "data_sources" / normalize_id(data_source_id)
        write_json(data_source_dir / "data_source.json", data_source)
        write_json(data_source_dir / "rows.json", page_rows)

        self.counts["data_sources"] += 1
        self.counts["database_rows"] += len(page_rows)
        self._bump_root_count(database_item.root_title, "data_sources")
        self._bump_root_count(database_item.root_title, "database_rows", len(page_rows))
        self._record_object(
            object_id=data_source_id,
            object_type="data_source",
            source=f"database:{database_id}",
            parent=data_source.get("parent", {}),
            files={
                "metadata": _relative(snapshot_dir, data_source_dir / "data_source.json"),
                "rows": _relative(snapshot_dir, data_source_dir / "rows.json"),
            },
            title=_database_title(data_source),
            extra={
                "database_id": database_id,
                "database_parent": data_source.get("database_parent", {}),
            },
        )
        return children

    def _hydrate_page_properties(self, page: dict[str, Any]) -> None:
        page_id = page.get("id")
        properties = page.get("properties")
        if not page_id or not isinstance(properties, dict):
            return
        for name, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            prop_type = prop.get("type")
            if prop_type not in _PAGINATED_PROPERTY_TYPES:
                continue
            items = prop.get(prop_type)
            if not isinstance(items, list):
                continue
            # Relation values carry an explicit has_more flag; the other list
            # types only reveal truncation by hitting the 25-reference cap.
            if not prop.get("has_more") and len(items) < _PROPERTY_REF_LIMIT:
                continue
            prop_id = prop.get("id")
            if not prop_id:
                continue
            try:
                results = self.client.retrieve_page_property_items(page_id, prop_id)
            except NotionApiError as exc:
                self._record_warning(
                    "property_pagination_failed",
                    page_id,
                    f"Could not fetch the complete {prop_type} value for property {name!r}; "
                    f"keeping the first {_PROPERTY_REF_LIMIT} references: {exc}",
                    {"property": name, "property_type": prop_type},
                )
                continue
            complete = [item[prop_type] for item in results if isinstance(item, dict) and prop_type in item]
            if len(complete) >= len(items):
                prop[prop_type] = complete
                if "has_more" in prop:
                    prop["has_more"] = False

    def _fetch_block_tree(self, block_id: str) -> list[dict[str, Any]]:
        root_blocks = self._safe_list_block_children(block_id, owner_id=block_id)
        stack = list(reversed(root_blocks))
        while stack:
            block = stack.pop()
            if block.get("has_children"):
                children = self._safe_list_block_children(block["id"], owner_id=block_id)
                block["children"] = children
                stack.extend(reversed(children))
            else:
                block["children"] = []
        return root_blocks

    def _safe_list_block_children(self, block_id: str, owner_id: str) -> list[dict[str, Any]]:
        try:
            return self.client.list_block_children(block_id)
        except NotionApiError as exc:
            self.counts["block_fetch_errors"] += 1
            message = f"Could not fetch children for block {block_id}: {exc}"
            self._record_warning(
                "block_children_unavailable",
                owner_id,
                message,
                {"block_id": block_id},
            )
            LOGGER.exception("Continuing after block child fetch failure for %s", block_id)
            return []

    def _should_continue_after_object_error(self, item: QueueItem) -> bool:
        traversal = self.config.get("backup", {}).get("traversal", {})
        if not traversal.get("continue_on_object_error", True):
            return False
        if item.source in {"config_root", "resolved_config_root", "top_level_discovery"}:
            return not traversal.get("fail_on_root_object_error", True)
        return True

    def _should_warn_for_inaccessible_child_database(self, item: QueueItem, exc: NotionApiError) -> bool:
        traversal = self.config.get("backup", {}).get("traversal", {})
        if not traversal.get("skip_inaccessible_child_databases", True):
            return False
        return (
            item.object_type == "database"
            and item.source == "child_database_block"
            and not _is_linked_view_error(exc)
            and (
                "does not contain any data sources accessible" in str(exc)
                or (exc.status_code in {400, 403, 404} and "database" in str(exc).lower())
            )
        )

    def _record_linked_view(self, snapshot_dir: Path, item: QueueItem, exc: NotionApiError) -> bool:
        if item.object_type != "database" or item.source != "child_database_block" or not _is_linked_view_error(exc):
            return False

        linked_view_dir = snapshot_dir / "linked_views" / normalize_id(item.object_id)
        title = item.title or "Untitled"
        note = (
            "Secondary linked database view block. Notion does not expose an accessible data source for "
            "this wrapper; the source collection must be captured by a primary database block elsewhere."
        )
        reference = {
            "block_id": item.object_id,
            "block_title": title,
            "type": "linked_database_view",
            "owner_id": item.owner_id,
            "parent": item.parent or {},
            "note": note,
            "api_error": str(exc),
        }
        if exc.body:
            reference["api_error_body"] = exc.body

        write_json(linked_view_dir / "view_reference.json", reference)

        self.counts["linked_views"] += 1
        self._record_object(
            object_id=item.object_id,
            object_type="linked_database_view",
            source=item.source,
            parent=item.parent or {},
            files={"reference": _relative(snapshot_dir, linked_view_dir / "view_reference.json")},
            title=title,
            extra={"note": note, "owner_id": item.owner_id},
        )
        LOGGER.info("Recorded linked database view wrapper %s", item.object_id)
        return True

    def _record_warning(self, code: str, object_id: str, message: str, details: dict[str, Any]) -> None:
        key = (code, object_id, repr(sorted(details.items())))
        if key in self._warning_keys:
            return
        self._warning_keys.add(key)
        self.warnings.append({"code": code, "object_id": object_id, "message": message, "details": details})

    def _bump_root_count(self, root_title: str, key: str, amount: int = 1) -> None:
        label = root_title or "(no configured root)"
        bucket = self.counts_by_root.setdefault(
            label, {"pages": 0, "databases": 0, "data_sources": 0, "database_rows": 0, "blocks": 0}
        )
        bucket[key] += amount

    def _record_error(self, code: str, object_id: str, message: str, details: dict[str, Any]) -> None:
        self.errors.append({"code": code, "object_id": object_id, "message": message, "details": details})

    def _record_object(
        self,
        object_id: str,
        object_type: str,
        source: str,
        parent: dict[str, Any],
        files: dict[str, str],
        title: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "id": object_id,
            "normalized_id": normalize_id(object_id),
            "type": object_type,
            "title": title,
            "source": source,
            "parent": parent,
            "files": files,
        }
        if extra:
            record.update(extra)
        self.manifest_objects.append(record)
        self.restore_map[object_id] = {
            "type": object_type,
            "title": title,
            "parent": parent,
            "files": files,
            **(extra or {}),
        }


def run_backup(config_path: str | None = None) -> Path:
    config = load_config(config_path)
    notion = config.get("notion", {})
    client = NotionClient(
        notion_version=notion.get("api_version", DEFAULT_NOTION_VERSION),
        timeout_seconds=int(notion.get("timeout_seconds", 180)),
        max_retries=int(notion.get("max_retries", 8)),
        page_size=int(notion.get("page_size", 100)),
        retry_initial_sleep_seconds=float(notion.get("retry_initial_sleep_seconds", 2)),
        retry_max_sleep_seconds=float(notion.get("retry_max_sleep_seconds", 120)),
        requests_per_second=float(notion.get("requests_per_second", 3.0)),
        burst=int(notion.get("burst", 8)),
    )
    return BackupRunner(config, client).run()


def _queue_children(blocks: list[dict[str, Any]], owner_id: str, root_title: str = "") -> list[QueueItem]:
    queue: list[QueueItem] = []
    stack = list(reversed(blocks))
    while stack:
        block = stack.pop()
        block_type = block.get("type")
        if block_type == "child_page":
            queue.append(QueueItem("page", block["id"], "child_page_block", owner_id=owner_id, root_title=root_title))
        elif block_type == "child_database":
            queue.append(
                QueueItem(
                    "database",
                    block["id"],
                    "child_database_block",
                    title=block.get("child_database", {}).get("title", ""),
                    parent=block.get("parent", {}),
                    owner_id=owner_id,
                    root_title=root_title,
                )
            )
        stack.extend(reversed(block.get("children", [])))
    return queue


def _count_blocks(blocks: list[dict[str, Any]]) -> int:
    count = 0
    stack = list(blocks)
    while stack:
        block = stack.pop()
        count += 1
        stack.extend(block.get("children", []))
    return count


def _database_title(database: dict[str, Any]) -> str:
    title = database.get("title", [])
    if not title:
        return "Untitled database"
    return "".join(item.get("plain_text", "") for item in title) or "Untitled database"


def _data_source_parent_database_id(data_source: dict[str, Any]) -> str | None:
    parent = data_source.get("parent")
    if isinstance(parent, dict) and parent.get("type") == "database_id":
        database_id = parent.get("database_id")
        if isinstance(database_id, str) and database_id:
            return database_id
    return None


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _status(warnings: list[dict[str, Any]], errors: list[dict[str, Any]]) -> str:
    if errors:
        return "partial"
    if warnings:
        return "warning"
    return "success"


def _is_linked_view_error(exc: NotionApiError) -> bool:
    message = ""
    if isinstance(exc.body, dict):
        if exc.body.get("status") != 400:
            return False
        if exc.body.get("code") != "validation_error":
            return False
        message = str(exc.body.get("message", ""))
    else:
        if exc.status_code != 400:
            return False
    if not message:
        message = str(exc)
    return "does not contain any data sources accessible by this API bot" in message


def _write_manifest_with_size(snapshot_dir: Path, manifest: dict[str, Any]) -> None:
    manifest_path = snapshot_dir / "manifest.json"

    # size_bytes contract: the snapshot payload excluding manifest.json itself.
    # Measuring before the manifest exists matches the storage-step rewrite,
    # which subtracts the manifest file from the directory total.
    total_bytes = directory_size_bytes(snapshot_dir)

    manifest.update(
        {
            "size_bytes": total_bytes,
            "size_human": format_bytes(total_bytes),
        }
    )

    write_json(manifest_path, manifest)


def _external_archive_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("storage", {}).get("external_archive", {}).get("enabled", False))


def _runner_retention_enabled(config: dict[str, Any]) -> bool:
    retention = config.get("retention", {})
    if not retention.get("enabled", True):
        return False
    return not _external_archive_enabled(config)
