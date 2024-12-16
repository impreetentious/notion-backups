from __future__ import annotations

import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from notion_backup.storage import manage_storage

class StorageTests(unittest.TestCase):
    def test_external_disabled_keeps_all_snapshots_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            archive_dir = Path(tmp) / "archives"
            names = ["NB_20260501_020000+0530", "NB_20260505_020000+0530", "NB_20260508_020000+0530"]
            for name in names:
                snapshot = output_dir / name
                snapshot.mkdir(parents=True)
                (snapshot / "manifest.json").write_text(json.dumps({"created_at": _created_at_from_name(name)}), encoding="utf-8")

            result = manage_storage(
                {
                    "backup": {"output_dir": str(output_dir), "archive_dir": str(archive_dir)},
                    "storage": {"external_archive": {"enabled": False}},
                }
            )

            self.assertEqual(result.status, "warning")
            for name in names:
                self.assertTrue((output_dir / name).exists())
            self.assertEqual(len(result.warnings), len(names))

    @patch("notion_backup.storage.shutil.which", return_value="/usr/bin/rclone")
    @patch("notion_backup.storage._run")
    def test_successful_upload_archives_and_deletes_all_snapshots(self, run_mock, _which_mock) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "[]"
        run_mock.return_value.stderr = ""
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            archive_dir = Path(tmp) / "archives"
            names = ["NB_20260501_020000+0530", "NB_20260505_020000+0530", "NB_20260508_020000+0530"]
            for name in names:
                snapshot = output_dir / name
                snapshot.mkdir(parents=True)
                (snapshot / "manifest.json").write_text(json.dumps({"created_at": _created_at_from_name(name)}), encoding="utf-8")

            result = manage_storage(
                {
                    "backup": {"output_dir": str(output_dir), "archive_dir": str(archive_dir)},
                    "retention": {"retain_days": 60},
                    "storage": {
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
            for name in names:
                self.assertFalse((output_dir / name).exists())
            self.assertEqual(len(result.uploaded_to_external), len(names))

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
            name = "NB_20260501_020000+0530"
            snapshot = output_dir / name
            snapshot.mkdir(parents=True)
            (snapshot / "manifest.json").write_text(json.dumps({"created_at": _created_at_from_name(name)}), encoding="utf-8")

            result = manage_storage(
                {
                    "backup": {"output_dir": str(output_dir), "archive_dir": str(archive_dir)},
                    "retention": {"retain_days": 60},
                    "storage": {
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
                    "Upload failed for NB_20260501_020000+0530; it was not archived.",
                    "Could not list external archives for retention cleanup.",
                ],
            )
            self.assertEqual(result.errors, ['oauth2: "invalid_grant" "Token has been expired or revoked."'])
            self.assertTrue((output_dir / "NB_20260501_020000+0530").exists())

    @patch("notion_backup.storage.shutil.which", return_value="/usr/bin/rclone")
    @patch("notion_backup.storage._run")
    def test_failed_upload_records_storage_status_in_the_current_manifest(self, run_mock, _which_mock) -> None:
        run_mock.side_effect = [
            subprocess.CompletedProcess(
                ["rclone", "copyto"], returncode=1, stdout="", stderr="upload boom"
            ),
            subprocess.CompletedProcess(["rclone", "lsjson"], returncode=0, stdout="[]", stderr=""),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            archive_dir = Path(tmp) / "archives"
            name = "NB_20260501_020000+0530"
            snapshot = output_dir / name
            snapshot.mkdir(parents=True)
            (snapshot / "manifest.json").write_text(
                json.dumps(
                    {
                        "created_at": _created_at_from_name(name),
                        "storage": {"destination": "github", "status": "pending", "warnings": [], "errors": []},
                    }
                ),
                encoding="utf-8",
            )

            result = manage_storage(
                {
                    "backup": {"output_dir": str(output_dir), "archive_dir": str(archive_dir)},
                    "retention": {"retain_days": 60},
                    "storage": {
                        "external_archive": {
                            "enabled": True,
                            "remote": "notionbackups",
                            "folder": "NotionBackups",
                            "delete_remote_older_than_retention": True,
                        },
                    },
                },
                current_snapshot_dir=snapshot,
            )

            # The snapshot survives the failed upload, and its manifest now carries the
            # archival warning/error so the notification step can surface it.
            self.assertEqual(result.status, "warning")
            self.assertTrue((snapshot / "manifest.json").exists())
            on_disk = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(on_disk["storage"]["status"], "warning")
            self.assertIn(
                "Upload failed for NB_20260501_020000+0530; it was not archived.",
                on_disk["storage"]["warnings"],
            )
            self.assertEqual(on_disk["storage"]["errors"], ["upload boom"])
            # The in-memory manifest used for the storage-summary fallback matches.
            self.assertEqual(result.current_manifest["storage"]["warnings"], on_disk["storage"]["warnings"])
            self.assertEqual(result.current_manifest["storage"]["errors"], ["upload boom"])

    @patch("notion_backup.storage.shutil.which", return_value="/usr/bin/rclone")
    @patch("notion_backup.storage._run")
    def test_cleanup_deletes_only_expired_remote_archives(self, run_mock, _which_mock) -> None:
        listing = json.dumps(
            [
                {"Name": "NB_expired.tar.gz", "ModTime": "2000-01-01T00:00:00+00:00"},
                {"Name": "NB_recent.tar.gz", "ModTime": "2099-01-01T00:00:00+00:00"},
                {"Name": "notes.txt", "ModTime": "2000-01-01T00:00:00+00:00"},
                {"Name": "NB_malformed.tar.gz", "ModTime": "not-a-timestamp"},
            ]
        )
        run_mock.side_effect = [
            subprocess.CompletedProcess(["rclone", "lsjson"], returncode=0, stdout=listing, stderr=""),
            subprocess.CompletedProcess(["rclone", "deletefile"], returncode=0, stdout="", stderr=""),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            archive_dir = Path(tmp) / "archives"
            output_dir.mkdir(parents=True)

            result = manage_storage(
                {
                    "backup": {"output_dir": str(output_dir), "archive_dir": str(archive_dir)},
                    "storage": {
                        "external_archive": {
                            "enabled": True,
                            "remote": "notionbackups",
                            "folder": "NotionBackups",
                            "retention_days": 30,
                            "delete_remote_older_than_retention": True,
                        },
                    },
                }
            )

            self.assertEqual(result.status, "success")
            delete_calls = [call for call in run_mock.call_args_list if call.args[0][1] == "deletefile"]
            self.assertEqual(len(delete_calls), 1)
            self.assertEqual(delete_calls[0].args[0][2], "notionbackups:NotionBackups/NB_expired.tar.gz")

    @patch("notion_backup.storage.shutil.which", return_value="/usr/bin/rclone")
    @patch("notion_backup.storage._run")
    def test_uploaded_archive_manifest_reflects_final_storage_state(self, run_mock, _which_mock) -> None:
        name = "NB_20260501_020000+0530"
        seen: dict = {}

        def _fake_run(command):
            if command[1] == "copyto":
                # Inspect the tar that would be sent to Drive: its manifest must
                # already carry the finalized storage block, not the "pending" scaffold.
                with tarfile.open(command[2], "r:gz") as tar:
                    member = next(m for m in tar.getmembers() if m.name.endswith("manifest.json"))
                    seen["storage"] = json.loads(tar.extractfile(member).read().decode("utf-8"))["storage"]
                return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(command, returncode=0, stdout="[]", stderr="")

        run_mock.side_effect = _fake_run
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "exports"
            archive_dir = Path(tmp) / "archives"
            snapshot = output_dir / name
            snapshot.mkdir(parents=True)
            (snapshot / "manifest.json").write_text(
                json.dumps(
                    {
                        "created_at": _created_at_from_name(name),
                        "storage": {
                            "destination": "github",
                            "status": "pending",
                            "uploaded_to_external": [],
                            "deleted_from_github": [],
                            "warnings": [],
                            "errors": [],
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = manage_storage(
                {
                    "backup": {"output_dir": str(output_dir), "archive_dir": str(archive_dir)},
                    "storage": {
                        "external_archive": {
                            "enabled": True,
                            "remote": "notionbackups",
                            "folder": "NotionBackups",
                            "delete_remote_older_than_retention": True,
                        },
                    },
                },
                current_snapshot_dir=snapshot,
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(seen["storage"]["status"], "success")
        self.assertEqual(seen["storage"]["destination"], "github+google_drive")
        self.assertEqual(
            seen["storage"]["uploaded_to_external"],
            ["notionbackups:NotionBackups/NB_20260501_020000+0530.tar.gz"],
        )


def _created_at_from_name(name: str) -> str:
    return f"{name[3:7]}-{name[7:9]}-{name[9:11]}T{name[12:14]}:{name[14:16]}:{name[16:18]}{name[18:]}"

if __name__ == "__main__":
    unittest.main()