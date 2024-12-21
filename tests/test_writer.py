from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notion_backup.writer import format_bytes, write_json, write_text


class AtomicWriteTests(unittest.TestCase):
    def test_write_json_round_trips_and_leaves_no_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "manifest.json"

            write_json(target, {"b": 2, "a": 1})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": 1, "b": 2})
            self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["manifest.json"])

    def test_write_text_preserves_unicode_without_escaping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "content.md"

            write_text(target, "Ground Zero 🌪️\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "Ground Zero 🌪️\n")

    def test_a_failed_write_keeps_the_previous_file_and_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "manifest.json"
            write_json(target, {"generation": 1})

            with patch("notion_backup.writer.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    write_json(target, {"generation": 2})

            # The old snapshot survives intact and no partial file is left behind.
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"generation": 1})
            self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["manifest.json"])

    def test_rewriting_a_file_replaces_it_rather_than_appending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "manifest.json"

            write_json(target, {"objects": [1, 2, 3]})
            write_json(target, {"objects": []})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"objects": []})


class FormatBytesTests(unittest.TestCase):
    def test_units_scale_and_bytes_stay_integral(self) -> None:
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1024), "1.00 KB")
        self.assertEqual(format_bytes(1024 * 1024), "1.00 MB")
        self.assertEqual(format_bytes(1024 ** 3), "1.00 GB")


if __name__ == "__main__":
    unittest.main()
