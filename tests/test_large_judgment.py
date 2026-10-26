import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests

from judgment_app.acquisition.web import (
    DOCUMENT_URL,
    PdfOnlyJudgment,
    TextOnlyJudgment,
    download_cases,
    get_judgment_pdf,
    get_judgment_web,
)
from judgment_app.case_number import JudgmentId


JID = JudgmentId.from_string("TPDM,109,金重訴,16,20201231,1")
RELATED = JudgmentId.from_string("TPHM,110,金上重訴,1,20220101,1")
PDF_PATH = "/FILES/TPDM/109%2c%e9%87%91%e9%87%8d%e8%a8%b4%2c16%2c20201231%2c1.pdf"
PDF_URL = "https://judgment.judicial.gov.tw" + PDF_PATH
PDF_BYTES = b"%PDF-1.7\nexample judgment"
TEXT_JID = JudgmentId.from_string("TPDM,101,金訴,60,20170703,5")
TEXT_PATH = "GetOverLimit.ashx?id=101%2c%e9%87%91%e8%a8%b4%2c60%2c20170703%2c5&court=TPDM"
TEXT_URL = "https://judgment.judicial.gov.tw/FJUD/" + TEXT_PATH
FULL_TEXT = "台灣台北地方法院刑事判決　101年度金訴字第60號\r\n    主　文\r\n判決全文。\r\n"


def response(content, status=200):
    result = requests.Response()
    result.status_code = status
    result.url = DOCUMENT_URL
    result._content = content.encode("utf-8") if isinstance(content, str) else content
    return result


def large_page(href=PDF_PATH):
    return response(
        '<meta charset="utf-8"><div class="jud_content"><div class="htmlcontent"></div>'
        '<div class="text-pre text-pre-in">由於裁判書全文大於 1 M，請'
        f'<a href="{href}" target="_blank">按此</a>下載檔案。'
        '<!-- 主文：[不應當成全文] --></div></div>'
    )


def text_file(content=FULL_TEXT):
    result = response(content)
    result.headers["Content-Type"] = "application/octet-stream"
    result.headers["Content-Disposition"] = "attachment;filename=TPDM_101_%e9%87%91%e8%a8%b4_60_20170703_5.txt"
    return result


class LargeJudgmentTests(unittest.TestCase):
    def test_getoverlimit_downloads_full_text_and_preserves_whitespace(self):
        session = Mock()
        session.get.side_effect = [large_page(TEXT_PATH.replace("&", "&amp;")), text_file()]
        result = get_judgment_web(TEXT_JID, session=session)
        self.assertEqual(result["text"], FULL_TEXT)
        self.assertEqual(result["jid"], TEXT_JID.to_string())
        session.get.assert_called_with(TEXT_URL, headers={"Referer": DOCUMENT_URL}, timeout=(10, 60))
        session.close.assert_not_called()

    def test_pdf_api_does_not_return_text_bytes_as_pdf(self):
        session = Mock()
        session.get.side_effect = [large_page(TEXT_PATH), text_file()]
        with self.assertRaises(TextOnlyJudgment) as raised:
            get_judgment_pdf(TEXT_JID, session=session)
        self.assertEqual(raised.exception.content, FULL_TEXT.encode("utf-8"))

    def test_txt_response_validation(self):
        for content in ("", "  ", "\ufeff<html>Error</html>", b"\xff\xfeinvalid"):
            with self.subTest(content=content):
                session = Mock()
                session.get.side_effect = [large_page(TEXT_PATH), text_file(content)]
                with self.assertRaises(RuntimeError):
                    get_judgment_web(TEXT_JID, session=session)
        session = Mock()
        session.get.side_effect = [large_page(TEXT_PATH), text_file("\ufeff" + FULL_TEXT)]
        self.assertEqual(get_judgment_web(TEXT_JID, session=session)["text"], FULL_TEXT)

    def test_getoverlimit_saves_txt_in_web_and_pdf_modes(self):
        for web, pdf in ((True, False), (False, True), (True, True)):
            with self.subTest(web=web, pdf=pdf), TemporaryDirectory() as folder, \
                    patch("judgment_app.acquisition.web.case_to_jids", return_value=[TEXT_JID]), \
                    patch("judgment_app.acquisition.web.requests.Session") as session_class:
                session = session_class.return_value.__enter__.return_value
                session.get.side_effect = [large_page(TEXT_PATH), text_file()] * 2
                report = download_cases([TEXT_JID.case_number], folder, web=web, pdf=pdf)
                target = Path(folder) / f"{TEXT_JID.file_name}.txt"
                self.assertEqual(report["files"], [str(target)])
                self.assertEqual(target.read_bytes(), FULL_TEXT.encode("utf-8"))
                self.assertFalse(report["errors"])
                self.assertEqual(list(Path(folder).iterdir()), [target])

                session.get.side_effect = [large_page(TEXT_PATH), text_file("新版本全文")]
                report = download_cases([TEXT_JID.case_number], folder, web=web, pdf=pdf)
                self.assertFalse(report["files"])
                self.assertFalse(report["errors"])
                self.assertIn(str(target), report["skipped"])
                self.assertEqual(target.read_bytes(), FULL_TEXT.encode("utf-8"))

    def test_history_getoverlimit_saves_txt_in_both_modes(self):
        for pdf in (False, True):
            with self.subTest(pdf=pdf), TemporaryDirectory() as folder, \
                    patch("judgment_app.acquisition.web.case_to_jids", return_value=[JID]), \
                    patch("judgment_app.acquisition.web.get_judgment_history", return_value=[TEXT_JID]), \
                    patch("judgment_app.acquisition.web.requests.Session") as session_class:
                session = session_class.return_value.__enter__.return_value
                session.get.side_effect = [large_page(TEXT_PATH), text_file()]
                case_folder = Path(folder) / JID.case_number.file_name
                case_folder.mkdir()
                (case_folder / f"{JID.file_name}{'.pdf' if pdf else '.txt'}").write_bytes(b"existing")
                report = download_cases([JID.case_number], folder, web=not pdf, pdf=pdf, history=True)
                target = case_folder / f"{TEXT_JID.file_name}.txt"
                self.assertEqual(report["history_files"], [str(target)])
                self.assertEqual(target.read_bytes(), FULL_TEXT.encode("utf-8"))
                self.assertFalse(report["errors"])

    def test_getoverlimit_can_also_return_pdf_without_redownloading(self):
        with TemporaryDirectory() as folder, \
                patch("judgment_app.acquisition.web.case_to_jids", return_value=[TEXT_JID]), \
                patch("judgment_app.acquisition.web.requests.Session") as session_class:
            session = session_class.return_value.__enter__.return_value
            session.get.side_effect = [large_page(TEXT_PATH), response(PDF_BYTES)]
            report = download_cases([TEXT_JID.case_number], folder, web=True)
            self.assertFalse(report["errors"])
            self.assertEqual(Path(report["files"][0]).read_bytes(), PDF_BYTES)
            self.assertEqual(Path(report["files"][0]).suffix, ".pdf")
            self.assertEqual(session.get.call_count, 2)

    def test_downloads_direct_pdf_links(self):
        for href in (PDF_PATH, PDF_URL, ".." + PDF_PATH):
            with self.subTest(href=href):
                session = Mock()
                session.get.side_effect = [large_page(href), response(PDF_BYTES)]
                self.assertEqual(get_judgment_pdf(JID, session=session), PDF_BYTES)
                session.get.assert_called_with(
                    PDF_URL, headers={"Referer": DOCUMENT_URL}, timeout=(10, 60)
                )
                session.close.assert_not_called()

    def test_web_does_not_return_notice_as_full_text(self):
        session = Mock()
        session.get.return_value = large_page()
        with self.assertRaises(PdfOnlyJudgment) as raised:
            get_judgment_web(JID, session=session)
        self.assertEqual(raised.exception.pdf_url, PDF_URL)
        self.assertEqual(raised.exception.page_url, DOCUMENT_URL)

    def test_regular_pdf_and_text_still_work(self):
        session = Mock()
        session.get.side_effect = [
            response('<a id="hlExportPDF" href="/FJUD/ExportPDF.aspx?id=test">PDF</a>'),
            response(PDF_BYTES),
            response('<meta charset="utf-8"><div class="jud_content">判決<br>正文</div>'),
        ]
        self.assertEqual(get_judgment_pdf(JID, session=session), PDF_BYTES)
        self.assertEqual(get_judgment_web(JID, session=session)["text"], "判決\n正文")

    def test_notice_link_takes_priority_over_export_button(self):
        page = large_page()
        page._content += b'<a id="hlExportPDF" href="/FJUD/ExportPDF.aspx">PDF</a>'
        session = Mock()
        session.get.side_effect = [page, response(PDF_BYTES)]
        self.assertEqual(get_judgment_pdf(JID, session=session), PDF_BYTES)
        self.assertEqual(session.get.call_args.args[0], PDF_URL)

    def test_rejects_missing_or_external_pdf_links(self):
        for getter in (get_judgment_web, get_judgment_pdf):
            with self.subTest(getter=getter.__name__):
                session = Mock()
                session.get.return_value = large_page("#")
                with self.assertRaisesRegex(RuntimeError, "找不到全文下載連結"):
                    getter(JID, session=session)
        session = Mock()
        session.get.return_value = large_page("https://example.com/test.pdf")
        with self.assertRaisesRegex(RuntimeError, "無法識別"):
            get_judgment_pdf(JID, session=session)
        self.assertEqual(session.get.call_count, 1)

    @patch("judgment_app.acquisition.web.time.sleep")
    def test_rejects_error_response_instead_of_saving_pdf(self, sleep):
        for bad_response, error in ((response("<html>Error</html>"), RuntimeError),
                                    (response("unavailable", 503), requests.HTTPError)):
            with self.subTest(error=error):
                session = Mock()
                session.get.side_effect = [large_page(), bad_response, bad_response, bad_response]
                with self.assertRaises(error):
                    get_judgment_pdf(JID, session=session)

    def test_web_download_falls_back_to_pdf_and_skips_existing_file(self):
        for pdf_enabled in (False, True):
            with self.subTest(pdf_enabled=pdf_enabled), TemporaryDirectory() as folder, \
                    patch("judgment_app.acquisition.web.case_to_jids", return_value=[JID]), \
                    patch("judgment_app.acquisition.web.requests.Session") as session_class:
                session = session_class.return_value.__enter__.return_value
                session.get.side_effect = [large_page(), response(PDF_BYTES)]
                messages = []
                report = download_cases([JID.case_number], folder, web=True, pdf=pdf_enabled,
                                        on_result=messages.append)
                target = Path(folder) / f"{JID.file_name}.pdf"
                self.assertEqual(report["files"], [str(target)])
                self.assertFalse(report["errors"])
                self.assertEqual(target.read_bytes(), PDF_BYTES)
                self.assertEqual(list(Path(folder).iterdir()), [target])
                self.assertTrue(any("改下載原始 PDF" in message for message in messages))
                self.assertEqual(session.get.call_count, 2)

                session.get.reset_mock()
                session.get.side_effect = [large_page()]
                report = download_cases([JID.case_number], folder, web=True, pdf=pdf_enabled)
                self.assertFalse(report["files"])
                self.assertFalse(report["errors"])
                self.assertIn(str(target), report["skipped"])
                self.assertEqual(session.get.call_count, 1)
                self.assertEqual(target.read_bytes(), PDF_BYTES)

    def test_history_full_text_falls_back_to_pdf(self):
        with TemporaryDirectory() as folder, \
                patch("judgment_app.acquisition.web.case_to_jids", return_value=[JID]), \
                patch("judgment_app.acquisition.web.get_judgment_history", return_value=[RELATED]), \
                patch("judgment_app.acquisition.web.requests.Session") as session_class:
            session = session_class.return_value.__enter__.return_value
            normal_page = response('<meta charset="utf-8"><div class="jud_content">判決正文</div>')
            session.get.side_effect = [normal_page, large_page(), response(PDF_BYTES)]
            report = download_cases([JID.case_number], folder, web=True, history=True)
            target = Path(folder) / JID.case_number.file_name / f"{RELATED.file_name}.pdf"
            self.assertEqual(report["history_files"], [str(target)])
            self.assertEqual(target.read_bytes(), PDF_BYTES)
            self.assertFalse(report["errors"])
            self.assertFalse(list(Path(folder).rglob(f"{RELATED.file_name}.txt")))

            session.get.reset_mock()
            session.get.side_effect = [large_page()]
            report = download_cases([JID.case_number], folder, web=True, history=True)
            self.assertEqual(report["history_skipped"], [str(target)])
            self.assertFalse(report["errors"])
            self.assertEqual(session.get.call_count, 1)

    @patch("judgment_app.acquisition.web.time.sleep")
    def test_failed_fallback_records_case_without_creating_files(self, sleep):
        for status in (200, 404, 503):
            with self.subTest(status=status), TemporaryDirectory() as folder, \
                    patch("judgment_app.acquisition.web.case_to_jids", return_value=[JID]), \
                    patch("judgment_app.acquisition.web.requests.Session") as session_class:
                session = session_class.return_value.__enter__.return_value
                session.get.side_effect = [large_page()] + [response("error page", status) for _ in range(3)]
                report = download_cases([JID.case_number], folder, web=True)
                self.assertEqual(len(report["errors"]), 1)
                self.assertEqual(report["failed_cases"], [JID.case_number])
                self.assertFalse(report["files"])
                self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
