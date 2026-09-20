import os
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from judgment_app import paths
from judgment_app import download_judgments as api
from judgment_app.exceptions import ApiResponseError


class PackagingTests(unittest.TestCase):
    def test_current_cache_replaces_stale_config_token(self):
        today = date.today().isoformat()
        with patch.dict(os.environ, {"API_TOKEN": "old", "API_TOKEN_DATE": "2000-01-01"}), patch.object(paths, "load_dotenv"), patch.object(paths, "dotenv_values", return_value={"API_TOKEN": "fresh", "API_TOKEN_DATE": today}):
            paths.load_settings()
            self.assertEqual(os.environ["API_TOKEN"], "fresh")

    def test_frozen_paths_ignore_working_and_extraction_directories(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            with patch.object(paths.sys, "frozen", True, create=True), patch.object(paths.sys, "executable", str(base / "portable" / "app.exe")), patch.dict(os.environ, {"LOCALAPPDATA": str(base / "profile")}):
                self.assertEqual(paths.application_dir(), base / "portable")
                self.assertEqual(paths.resolve_output_path("data/judgments"), base / "profile" / "JudgmentApp" / "data" / "judgments")
                self.assertEqual(paths.config_file(), base / "profile" / "JudgmentApp" / ".env")
                (base / "portable").mkdir()
                (base / "portable" / ".env").touch()
                self.assertEqual(paths.config_file(), base / "portable" / ".env")

    def test_token_cache_write_failure_does_not_break_download(self):
        with patch.dict(os.environ, {"API_TOKEN": "", "API_TOKEN_DATE": ""}), patch.object(api, "request_new_token", return_value="new-token"), patch.object(api, "set_key", side_effect=PermissionError):
            self.assertEqual(api.get_token(), "new-token")

    def test_empty_daily_list_is_valid(self):
        with patch.object(api, "post_json", return_value=[{"date": "2026-09-20", "list": []}]):
            self.assertEqual(api.get_jid_json("token")["list"], [])

    def test_missing_api_token_is_a_handled_error(self):
        with patch.dict(os.environ, {"JUDICIAL_USERNAME": "test", "JUDICIAL_PASSWORD": "test"}), patch.object(api, "post_json", return_value={"message": "invalid"}):
            with self.assertRaises(ApiResponseError):
                api.request_new_token()
