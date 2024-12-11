from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from notion_backup.retention import _created_at, _snapshot_dirs, apply_retention

class RetentionTimestampTests(unittest.TestCase):
    def test_manifest_timestamp_with_timezone_normalizes_to_utc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot-with-manifest"
            snapshot.mkdir()
            (snapshot / "manifest.json").write_text(
                json.dumps({"created_at": "2026-05-18T02:25:58+05:30"}),
                encoding="utf-8",
            )

            self.assertEqual(
                _created_at(snapshot),
                datetime(2026, 5, 17, 20, 55, 58, tzinfo=timezone.utc),
            )

    def test_folder_fallback_timestamp_with_offset_normalizes_to_utc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "2026-05-18T02-25-58+0530"
            snapshot.mkdir()

            self.assertEqual(
                _created_at(snapshot),
                datetime(2026, 5, 17, 20, 55, 58, tzinfo=timezone.utc),
            )

    def test_nb_folder_fallback_timestamp_with_offset_normalizes_to_utc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "NB_20260518_020000+0530"
            snapshot.mkdir()

            self.assertEqual(
                _created_at(snapshot),
                datetime(2026, 5, 17, 20, 30, 0, tzinfo=timezone.utc),
            )

    def test_folder_fallback_timestamp_without_offset_is_treated_as_utc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "2026-05-18T02-25-58"
            snapshot.mkdir()

            self.assertEqual(
                _created_at(snapshot),
                datetime(2026, 5, 18, 2, 25, 58, tzinfo=timezone.utc),
            )

    def test_mixed_snapshot_timestamps_sort_without_type_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            archive_dir = Path(tmp) / "archives"
            output_dir.mkdir()
            archive_dir.mkdir()

            old_manifest = output_dir / "old-manifest"
            old_manifest.mkdir()
            (old_manifest / "manifest.json").write_text(
                json.dumps({"created_at": "2026-02-01T08:00:00+05:30"}),
                encoding="utf-8",
            )

            old_folder = output_dir / "NB_20260202_080000+0530"
            old_folder.mkdir()
            recent_folder = output_dir / "2026-05-18T02-25-58+0530"
            recent_folder.mkdir()

            snapshots = _snapshot_dirs(output_dir)
            self.assertEqual(
                [snapshot.path.name for snapshot in snapshots],
                ["old-manifest", "NB_20260202_080000+0530", "2026-05-18T02-25-58+0530"],
            )

            deleted = apply_retention(
                output_dir=output_dir,
                archive_dir=archive_dir,
                retain_days=60,
                min_snapshots=1,
                now=datetime(2026, 5, 18, tzinfo=timezone.utc),
            )

            self.assertEqual([path.name for path in deleted], ["old-manifest", "NB_20260202_080000+0530"])
            self.assertFalse(old_manifest.exists())
            self.assertFalse(old_folder.exists())
            self.assertTrue(recent_folder.exists())

    def test_min_snapshots_floor_caps_deletions_even_when_all_are_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            archive_dir = Path(tmp) / "archives"
            output_dir.mkdir()
            archive_dir.mkdir()

            names = [
                "NB_20260101_000000+0000",
                "NB_20260102_000000+0000",
                "NB_20260103_000000+0000",
                "NB_20260104_000000+0000",
                "NB_20260105_000000+0000",
            ]
            for name in names:
                (output_dir / name).mkdir()

            deleted = apply_retention(
                output_dir=output_dir,
                archive_dir=archive_dir,
                retain_days=30,
                min_snapshots=2,
                now=datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

            # Every snapshot predates the 30-day cutoff, but the min_snapshots
            # floor caps deletions at len(snapshots) - min_snapshots = 5 - 2 = 3.
            self.assertEqual(
                [path.name for path in deleted],
                ["NB_20260101_000000+0000", "NB_20260102_000000+0000", "NB_20260103_000000+0000"],
            )
            self.assertTrue((output_dir / "NB_20260104_000000+0000").exists())
            self.assertTrue((output_dir / "NB_20260105_000000+0000").exists())


if __name__ == "__main__":
    unittest.main()