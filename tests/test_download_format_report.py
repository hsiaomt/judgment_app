import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from judgment_app.acquisition.web import PdfOnlyJudgment, TextOnlyJudgment, download_cases
from judgment_app.case_number import JudgmentId
from judgment_app.gui import CaseReaderApp


JID = JudgmentId.from_string("TPDM,101,金訴,60,20170703,5")
RELATED = JudgmentId.from_string("TPHM,109,金上訴,1,20201231,1")
PDF = b"%PDF-test"


class FormatReportTests(unittest.TestCase):
    def test_format_substitution_and_missing_format_are_mutually_exclusive(self):
        for actual in ("txt", "pdf"):
            for web, pdf in ((True, False), (False, True), (True, True)):
                with self.subTest(actual=actual, web=web, pdf=pdf), TemporaryDirectory() as folder, \
                        patch("judgment_app.acquisition.web.case_to_jids", return_value=[JID]), \
                        patch("judgment_app.acquisition.web.get_judgment_web") as get_web, \
                        patch("judgment_app.acquisition.web.get_judgment_pdf") as get_pdf:
                    if actual == "txt":
                        get_web.return_value = {"text": "正文"}
                        get_pdf.side_effect = TextOnlyJudgment("url", "正文".encode("utf-8"))
                    else:
                        get_web.side_effect = PdfOnlyJudgment("url", "page", PDF)
                        get_pdf.return_value = PDF
                    for run in range(2):
                        report = download_cases([JID.case_number], folder, web=web, pdf=pdf)
                        expected_key = ("missing_formats" if web and pdf else
                                        "txt_to_pdf" if web and actual == "pdf" else
                                        "pdf_to_txt" if pdf and actual == "txt" else None)
                        for key in ("txt_to_pdf", "pdf_to_txt", "missing_formats"):
                            self.assertEqual(len(report[key]), int(key == expected_key))
                        if expected_key:
                            self.assertEqual(report[expected_key][0]["jid"], JID)
                        if web and pdf:
                            self.assertEqual(report["missing_formats"][0]["missing_formats"],
                                             ["PDF" if actual == "txt" else "TXT"])
                        self.assertFalse(report["errors"])
                        self.assertEqual(len(report["files"]), 1 if run == 0 else 0)
                        self.assertEqual(len(report["skipped_documents"]), len(report["skipped"]))
                        for entry in report["skipped_documents"]:
                            self.assertEqual(entry["jid"], JID)
                            self.assertEqual(entry["skipped_format"], actual.upper())
                            self.assertIsNone(entry["original_case"])

    def test_both_existing_formats_are_not_reported_as_missing(self):
        with TemporaryDirectory() as folder, \
                patch("judgment_app.acquisition.web.case_to_jids", return_value=[JID]), \
                patch("judgment_app.acquisition.web.get_judgment_web") as get_web, \
                patch("judgment_app.acquisition.web.get_judgment_pdf") as get_pdf:
            for suffix in (".txt", ".pdf", ".json"):
                (Path(folder) / f"{JID.file_name}{suffix}").write_bytes(b"existing")
            report = download_cases([JID.case_number], folder, web=True, pdf=True, api=True)
            self.assertEqual(len(report["skipped"]), 3)
            self.assertEqual([entry["skipped_format"] for entry in report["skipped_documents"]], ["TXT", "PDF", "JSON"])
            self.assertTrue(all(entry["jid"] == JID for entry in report["skipped_documents"]))
            self.assertEqual(report["missing_formats"], [])
            get_web.assert_not_called()
            get_pdf.assert_not_called()

    def test_missing_formats_are_tracked_per_judgment_date(self):
        later = JudgmentId.from_string("TPDM,101,金訴,60,20170801,1")
        with TemporaryDirectory() as folder, \
                patch("judgment_app.acquisition.web.case_to_jids", return_value=[JID, later]), \
                patch("judgment_app.acquisition.web.get_judgment_web", side_effect=[{"text": "正文"}, RuntimeError("TXT 失敗")]), \
                patch("judgment_app.acquisition.web.get_judgment_pdf", side_effect=[RuntimeError("PDF 失敗"), PDF]):
            report = download_cases([JID.case_number], folder, web=True, pdf=True)
            self.assertEqual([(entry["jid"], entry["missing_formats"]) for entry in report["missing_formats"]],
                             [(JID, ["PDF"]), (later, ["TXT"])])
            self.assertEqual(len(report["errors"]), 2)

    def test_both_failures_report_both_missing_formats(self):
        with TemporaryDirectory() as folder, \
                patch("judgment_app.acquisition.web.case_to_jids", return_value=[JID]), \
                patch("judgment_app.acquisition.web.get_judgment_web", side_effect=RuntimeError("TXT 失敗")), \
                patch("judgment_app.acquisition.web.get_judgment_pdf", side_effect=RuntimeError("PDF 失敗")):
            report = download_cases([JID.case_number], folder, web=True, pdf=True)
            self.assertEqual(report["missing_formats"][0]["missing_formats"], ["TXT", "PDF"])
            self.assertFalse(report["txt_to_pdf"])
            self.assertFalse(report["pdf_to_txt"])

    def test_history_format_entries_keep_original_case(self):
        for web, pdf, actual, key in ((True, False, "pdf", "txt_to_pdf"),
                                      (False, True, "txt", "pdf_to_txt"),
                                      (True, True, "txt", "missing_formats")):
            with self.subTest(key=key), TemporaryDirectory() as folder, \
                    patch("judgment_app.acquisition.web.case_to_jids", return_value=[JID]), \
                    patch("judgment_app.acquisition.web.get_judgment_history", return_value=[RELATED]), \
                    patch("judgment_app.acquisition.web.get_judgment_web") as get_web, \
                    patch("judgment_app.acquisition.web.get_judgment_pdf") as get_pdf:
                case_folder = Path(folder) / JID.case_number.file_name
                case_folder.mkdir()
                for suffix in (".txt", ".pdf"):
                    (case_folder / f"{JID.file_name}{suffix}").write_bytes(b"existing")
                get_web.return_value = {"text": "正文"}
                get_pdf.return_value = PDF
                if actual == "pdf":
                    get_web.side_effect = PdfOnlyJudgment("url", "page", PDF)
                else:
                    get_pdf.side_effect = TextOnlyJudgment("url", b"text")
                report = download_cases([JID.case_number], folder, web=web, pdf=pdf, history=True)
                self.assertEqual(len(report[key]), 1)
                self.assertEqual(report[key][0]["jid"], RELATED)
                self.assertEqual(report[key][0]["original_case"], JID.case_number)
                report = download_cases([JID.case_number], folder, web=web, pdf=pdf, history=True)
                self.assertEqual(len(report["history_skipped_documents"]), len(report["history_skipped"]))
                self.assertTrue(report["history_skipped_documents"])
                for entry in report["history_skipped_documents"]:
                    self.assertEqual(entry["jid"], RELATED)
                    self.assertEqual(entry["original_case"], JID.case_number)
                    self.assertEqual(entry["skipped_format"], actual.upper())


class FormatSummaryTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = CaseReaderApp(self.root)

    def tearDown(self):
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.root.destroy()

    def test_summary_sections_precede_empty_cases_and_show_missing_format(self):
        report = dict(files=[], history_files=[], skipped=["existing.txt"], history_skipped=[], empty=[], errors=[],
                      skipped_documents=[{"jid": JID, "original_case": None, "skipped_format": "TXT"}],
                      txt_to_pdf=[{"jid": JID, "original_case": None}],
                      pdf_to_txt=[{"jid": RELATED, "original_case": JID.case_number}],
                      missing_formats=[{"jid": RELATED, "original_case": JID.case_number, "missing_formats": ["PDF"]}])
        self.app.events.put(("download", report, None))
        self.app.poll()
        text = self.app.log.get("1.0", "end")
        titles = ["【只勾選 TXT，但改存 PDF 的案號】", "【只勾選 PDF，但改存 TXT 的案號】",
                  "【TXT、PDF 都勾選，但有格式未下載的案號】", "【無符合結果或未公開的案號】"]
        offsets = [text.index(title) for title in titles]
        self.assertEqual(offsets, sorted(offsets))
        skipped = text[text.index("【原始判決已存在而跳過的案號】"):offsets[0]]
        self.assertIn(JID.case_number.to_string(), skipped)
        self.assertIn("跳過格式：TXT", skipped)
        self.assertIn(JID.case_number.to_string(), text[offsets[0]:offsets[1]])
        self.assertIn(f"原始案號：{JID.case_number.to_string()}", text[offsets[1]:offsets[2]])
        self.assertIn("未下載：PDF", text[offsets[2]:offsets[3]])
        self.assertIn(f"裁判日期：{RELATED.date}", text[offsets[2]:offsets[3]])

    def test_existing_summary_is_preserved_with_history_skips_below(self):
        report = dict(files=["file"] * 96, history_files=["history"] * 84,
                      skipped=[], history_skipped=["existing"] * 4, empty=[JID.case_number] * 2, errors=[],
                      history_skipped_documents=[
                          {"jid": jid, "original_case": JID.case_number, "skipped_format": suffix}
                          for jid in (JID, RELATED) for suffix in ("TXT", "PDF")
                      ])
        self.app.events.put(("download", report, None))
        self.app.poll()
        text = self.app.log.get("1.0", "end")
        summary = "下載完成：原始判決 96 個檔案；歷審判決 84 個檔案；原始 0 個已存在並跳過；歷審 4 個已存在並跳過；2 個案號無符合結果；0 項失敗。"
        self.assertEqual(self.app.status.get(), summary)
        self.assertTrue(text.startswith(summary + "\n"))
        start = text.index("【歷審判決已存在而跳過的案號】")
        end = text.index("【只勾選 TXT，但改存 PDF 的案號】")
        skipped = text[start:end]
        self.assertEqual(skipped.count("跳過格式："), 4)
        self.assertIn(RELATED.case_number.to_string(), skipped)
        self.assertIn(f"原始案號：{JID.case_number.to_string()}", skipped)

    def test_empty_sections_show_none_for_older_reports(self):
        self.app.events.put(("download", dict(files=[], skipped=[], empty=[], errors=[]), None))
        self.app.poll()
        text = self.app.log.get("1.0", "end")
        for title in ("原始判決已存在而跳過的案號", "歷審判決已存在而跳過的案號",
                      "只勾選 TXT，但改存 PDF 的案號", "只勾選 PDF，但改存 TXT 的案號",
                      "TXT、PDF 都勾選，但有格式未下載的案號"):
            self.assertIn(f"【{title}】\n無", text)


if __name__ == "__main__":
    unittest.main()
