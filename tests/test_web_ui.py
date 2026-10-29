"""Exercise the local web adapter without contacting the judicial website."""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote

import pandas as pd
import requests

from judgment_app.case_number import CaseNumber, JudgmentId
from judgment_app.web_ui.server import create_server


class LocalWebTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.output = self.root / "web-downloads"
        self.server = create_server(port=0, output_dir=self.output)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
        )
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.session = requests.Session()
        self.session.trust_env = False
        self.addCleanup(self.session.close)
        response = self.session.get(f"{self.base}/api/config", timeout=5)
        self.assertEqual(response.status_code, 200, response.text)
        self.config = response.json()
        self.token = self.config["request_token"]

    def stop_server(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def post(self, route, payload, *, expected=200, headers=None):
        request_headers = {"X-Request-Token": self.token}
        request_headers.update(headers or {})
        response = self.session.post(
            self.base + route, json=payload, headers=request_headers, timeout=5,
        )
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def upload(self, content):
        response = self.session.post(
            self.base + "/api/workbooks", data=content,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Filename": quote("原始案號.xlsx"),
                "X-Request-Token": self.token,
            }, timeout=5,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def workbook(self):
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame([["說明"]]).to_excel(
                writer, sheet_name="說明", index=False, header=False,
            )
            pd.DataFrame([
                ["資料來源"],
                [None] * 26 + ["法院", "年度", "字別", "號數"],
                [None] * 26 + ["台北地院", 114, " 訴 ", 219],
                [None] * 26 + ["TPDM", "114.0", "訴", "219.0"],
                [None] * 26 + ["未知法院", 114, "訴", 220],
                [None] * 26 + ["TPD", None, "訴", 221],
                [None] * 26 + ["TPD", 0, "訴", 0],
            ]).to_excel(writer, sheet_name="案號", index=False, header=False)
        return buffer.getvalue()

    def download_payload(self, cases):
        return {
            "cases": [case.to_dict() for case in cases], "web": True,
            "pdf": False, "api": False, "history": True,
            "output": str(self.output),
        }

    def job(self, job_id, *, after=0):
        response = self.session.get(
            f"{self.base}/api/downloads/{job_id}", params={"after": after}, timeout=5,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def await_job(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.job(job_id)
            if result["status"] != "running":
                return result
            threading.Event().wait(0.01)
        self.fail("The mocked download job did not finish")

    def test_default_settings_preserve_download_page_choices(self):
        self.assertEqual(
            {key: self.config["defaults"][key] for key in ("web", "pdf", "api", "history")},
            {"web": True, "pdf": False, "api": False, "history": True},
        )
        self.assertEqual(Path(self.config["defaults"]["output"]), self.output)
        courts = {court["code"]: court for court in self.config["courts"]}
        self.assertEqual(courts["TPD"]["name"], "臺灣臺北地方法院")
        self.assertIn("北院", courts["TPD"]["keywords"])

    def test_excel_auto_detection_dedup_warnings_and_source_preservation(self):
        content = self.workbook()
        source = self.root / "original.xlsx"
        source.write_bytes(content)
        workbook = self.upload(source.read_bytes())
        self.assertEqual(workbook["sheets"], ["說明", "案號"])
        selection = {"workbook_id": workbook["workbook_id"], "sheet": "案號"}
        layout = self.post("/api/layout", selection)
        self.assertEqual(layout, {"columns": ["AA", "AB", "AC", "AD"], "start_row": 3})
        result = self.post("/api/import", {**selection, **layout})
        self.assertEqual(
            [(case["court"], case["year"], case["case"], case["number"]) for case in result["cases"]],
            [("TPD", 114, "訴", 219), ("TPD", 0, "訴", 0)],
        )
        self.assertEqual(len(result["warnings"]), 2)
        self.assertIn("第 5 列", result["warnings"][0])
        self.assertIn("第 6 列", result["warnings"][1])
        self.assertEqual(source.read_bytes(), content)
        repeated = self.post("/api/import", {**selection, **layout})
        self.assertEqual(repeated, result)
        self.assertEqual(source.read_bytes(), content)

    def test_invalid_import_settings_return_errors_without_corrupting_upload(self):
        workbook = self.upload(self.workbook())
        settings = {
            "workbook_id": workbook["workbook_id"], "sheet": "案號",
            "columns": ["AA", "AB", "AC", "AD"], "start_row": 3,
        }
        for changed in (
            {"columns": ["AA", "AA", "AC", "AD"]},
            {"columns": ["AA", "AB", "AC", "ZZZ"]},
            {"start_row": 0}, {"start_row": True},
        ):
            with self.subTest(changed=changed):
                error = self.post("/api/import", {**settings, **changed}, expected=400)
                self.assertTrue(error["error"])
        self.assertEqual(len(self.post("/api/import", settings)["cases"]), 2)

    def test_manual_case_requires_positive_ascii_integers(self):
        valid = {"court": "TPD", "year": "114", "case": " 訴 ", "number": "219"}
        case = self.post("/api/cases/validate", valid)["case"]
        self.assertEqual((case["court"], case["year"], case["case"], case["number"]),
                         ("TPD", 114, "訴", 219))
        for changed in (
            {"year": "0"}, {"number": "0"}, {"year": "１１４"},
            {"number": "2.0"}, {"year": True}, {"case": " "},
            {"court": "不存在的法院"},
        ):
            with self.subTest(changed=changed):
                self.post("/api/cases/validate", {**valid, **changed}, expected=400)

    def test_download_preserves_queue_order_isolates_output_and_serializes_report(self):
        cases = [CaseNumber("TPD", 114, "訴", 30), CaseNumber("TPD", 0, "訴", 0)]
        original = JudgmentId(cases[0], "20251001", 1, "M")
        related = JudgmentId(CaseNumber("TPH", 114, "上訴", 8), "20251002", 0, "M")
        existing = self.output / "existing.txt"
        self.output.mkdir()
        existing.write_bytes(b"keep existing judgment")
        observed = []

        def download(snapshot, output, **options):
            observed.append((snapshot, Path(output), options))
            for index, case in enumerate(snapshot, 1):
                options["progress"](f"處理 {index}/{len(snapshot)}：{case.to_string(ch=False)}")
                options["on_result"](f"下載成功：{case.to_string()}")
            target = Path(output) / "download.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"new judgment")
            return {
                "files": [str(target)], "history_files": [],
                "skipped": [str(existing)], "history_skipped": [],
                "empty": [cases[1]], "errors": ["格式下載失敗", "另一個下載失敗"],
                "failed_cases": [cases[0]],
                "skipped_documents": [{"jid": original, "original_case": None, "skipped_format": "TXT"}],
                "history_skipped_documents": [], "txt_to_pdf": [], "pdf_to_txt": [],
                "missing_formats": [{"jid": related, "original_case": cases[0], "missing_formats": ["PDF"]}],
            }

        with patch("judgment_app.web_ui.server.download_cases", side_effect=download):
            started = self.post("/api/downloads", self.download_payload(cases), expected=202)
            finished = self.await_job(started["job_id"])
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(observed[0][0], cases)
        actual_output = observed[0][1]
        self.assertNotEqual(actual_output, self.output)
        self.assertTrue(actual_output.is_relative_to(self.output))
        self.assertEqual(Path(started["output"]), actual_output)
        self.assertEqual(Path(finished["output"]), actual_output)
        self.assertTrue(observed[0][2]["history"])
        self.assertEqual(existing.read_bytes(), b"keep existing judgment")
        self.assertEqual(finished["total"], 2)
        self.assertEqual(finished["completed_cases"], 2)
        self.assertEqual(len(finished["report"]["files"]), 1)
        self.assertEqual(len(finished["report"]["errors"]), 2)
        self.assertEqual(len(finished["report"]["failed_cases"]), 1)
        self.assertEqual(finished["report"]["empty"][0]["number"], 0)
        missing = finished["report"]["missing_formats"][0]
        self.assertEqual(missing["missing_formats"], ["PDF"])
        self.assertEqual(missing["original_case"]["number"], 30)
        self.assertIn("20251002", str(missing["jid"]))
        events = finished["events"]
        self.assertTrue(any(event["kind"] == "progress" for event in events))
        self.assertTrue(any(event["kind"] == "result" for event in events))
        self.assertEqual([event["id"] for event in events], list(range(1, len(events) + 1)))
        self.assertEqual(self.job(started["job_id"], after=finished["next_cursor"])["events"], [])

    def test_running_job_rejects_second_download_and_counts_only_finished_cases(self):
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        case = CaseNumber("TPD", 114, "訴", 1)

        def download(cases, output, **options):
            options["progress"](f"處理 1/1：{case.to_string(ch=False)}")
            entered.set()
            release.wait(5)
            return {"files": [], "empty": [], "errors": [], "skipped": []}

        with patch("judgment_app.web_ui.server.download_cases", side_effect=download):
            started = self.post("/api/downloads", self.download_payload([case]), expected=202)
            self.assertTrue(entered.wait(5))
            running = self.job(started["job_id"])
            self.assertEqual(running["status"], "running")
            self.assertEqual(running["completed_cases"], 0)
            self.post("/api/downloads", self.download_payload([case]), expected=409)
            release.set()
            self.assertEqual(self.await_job(started["job_id"])["status"], "completed")

    def test_unexpected_download_error_becomes_failed_job(self):
        with patch("judgment_app.web_ui.server.download_cases", side_effect=RuntimeError("測試錯誤內容")):
            started = self.post(
                "/api/downloads", self.download_payload([CaseNumber("TPD", 114, "訴", 1)]),
                expected=202,
            )
            failed = self.await_job(started["job_id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("測試錯誤內容", failed["error"])

    def test_local_server_rejects_foreign_hosts_origins_and_missing_tokens(self):
        case = {"court": "TPD", "year": 114, "case": "訴", "number": 1}
        for headers in ({}, {"X-Request-Token": "incorrect"}):
            with self.subTest(headers=headers):
                response = self.session.post(
                    self.base + "/api/cases/validate", json=case, headers=headers, timeout=5,
                )
                self.assertEqual(response.status_code, 403, response.text)
        self.post("/api/cases/validate", case, expected=403,
                  headers={"Origin": "https://unrelated.example"})
        response = self.session.get(
            self.base + "/api/config", headers={"Host": "unrelated.example"}, timeout=5,
        )
        self.assertEqual(response.status_code, 403, response.text)
        response = self.session.get(self.base + "/%2e%2e/pyproject.toml", timeout=5)
        self.assertEqual(response.status_code, 404, response.text)
        self.post("/api/cases/validate", case, headers={"Origin": self.base})


if __name__ == "__main__":
    unittest.main()
