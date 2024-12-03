from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notion_backup.storage import manage_storage


class StorageTests(unittest.TestCase):
    def test_external_disabled_keeps_older_snapshots_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            archive_dir = Path(tmp) / "archives"
            for name in ["NB_20260501_020000+0530", "NB_20260505_020000+0530", "NB_20260508_020000+0530"]:
                snapshot = output_dir / name
                snapshot.mkdir(parents=True)
                (snapshot / "manifest.json").write_text(json.dumps({"created_at": _created_at_from_name(name)}), encoding="utf-8")

            result = manage_storage(
                {
                    "backup": {"output_dir": str(output_dir), "archive_dir": str(archive_dir)},
                    "storage": {"github": {"keep_latest_snapshots": 2}, "external_archive": {"enabled": False}},
                }
            )

            self.assertEqual(result.status, "warning")
            self.assertTrue((output_dir / "NB_20260501_020000+0530").exists())
            self.assertEqual(result.retained_on_github, ["NB_20260505_020000+0530", "NB_20260508_020000+0530"])

    @patch("notion_backup.storage.shutil.which", return_value="/usr/bin/rclone")
    @patch("notion_backup.storage._run")
    def test_successful_upload_deletes_only_older_snapshot(self, run_mock, _which_mock) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "[]"
        run_mock.return_value.stderr = ""
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            archive_dir = Path(tmp) / "archives"
            for name in ["NB_20260501_020000+0530", "NB_20260505_020000+0530", "NB_20260508_020000+0530"]:
                snapshot = output_dir / name
                snapshot.mkdir(parents=True)
                (snapshot / "manifest.json").write_text(json.dumps({"created_at": _created_at_from_name(name)}), encoding="utf-8")

            result = manage_storage(
                {
                    "backup": {"output_dir": str(output_dir), "archive_dir": str(archive_dir)},
                    "retention": {"retain_days": 60},
                    "storage": {
                        "github": {"keep_latest_snapshots": 2},
                        "external_archive": {
                            "enabled": True,
                            "remote": "notionbackups",
                            "folder": "NotionBackups",
                            "delete_remote_older_than_retention": True,
                        },
                    },
                }
            )

            self.assertEqual(result.status, "success")
            self.assertFalse((output_dir / "NB_20260501_020000+0530").exists())
            self.assertTrue((output_dir / "NB_20260505_020000+0530").exists())
            self.assertTrue((output_dir / "NB_20260508_020000+0530").exists())
            self.assertEqual(len(result.uploaded_to_external), 1)

    @patch("notion_backup.storage.shutil.which", return_value="/usr/bin/rclone")
    @patch("notion_backup.storage._run")
    def test_rclone_exit_codes_are_recorded_as_storage_warnings_and_errors(self, run_mock, _which_mock) -> None:
        run_mock.side_effect = [
            subprocess.CompletedProcess(
                ["rclone", "copyto"],
                returncode=1,
                stdout="",
                stderr='oauth2: "invalid_grant" "Token has been expired or revoked."',
            ),
            subprocess.CompletedProcess(["rclone", "lsjson"], returncode=1, stdout="", stderr="could not list"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            archive_dir = Path(tmp) / "archives"
            for name in ["NB_20260501_020000+0530", "NB_20260505_020000+0530", "NB_20260508_020000+0530"]:
                snapshot = output_dir / name
                snapshot.mkdir(parents=True)
                (snapshot / "manifest.json").write_text(json.dumps({"created_at": _created_at_from_name(name)}), encoding="utf-8")

            result = manage_storage(
                {
                    "backup": {"output_dir": str(output_dir), "archive_dir": str(archive_dir)},
                    "retention": {"retain_days": 60},
                    "storage": {
                        "github": {"keep_latest_snapshots": 2},
                        "external_archive": {
                            "enabled": True,
                            "remote": "notionbackups",
                            "folder": "NotionBackups",
                            "delete_remote_older_than_retention": True,
                        },
                    },
                }
            )

            self.assertEqual(result.status, "warning")
            self.assertEqual(
                result.warnings,
                [
                    "Upload failed for NB_20260501_020000+0530; keeping it on GitHub.",
                    "Could not list external archives for retention cleanup.",
                ],
            )
            self.assertEqual(result.errors, ['oauth2: "invalid_grant" "Token has been expired or revoked."'])
            self.assertTrue((output_dir / "NB_20260501_020000+0530").exists())


def _created_at_from_name(name: str) -> str:
    return f"{name[3:7]}-{name[7:9]}-{name[9:11]}T{name[12:14]}:{name[14:16]}:{name[16:18]}{name[18:]}"


if __name__ == "__main__":
    unittest.main()
