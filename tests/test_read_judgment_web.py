import unittest
import json
from unittest.mock import Mock, patch
from tempfile import TemporaryDirectory
from pathlib import Path

import requests

from judgment_app.case_number import CaseNumber
from judgment_app.read_judgment_web import MeasuredSession, get_judgment_web, get_judgment_pdf, download_cases, save_document, run_batch, download_case_pdfs
from judgment_app.read_judgment_web import case_to_jids


class WebJudgmentTests(unittest.TestCase):
    @patch("judgment_app.download_judgments.get_judgment")
    @patch("judgment_app.download_judgments.get_token", return_value="test-token")
    @patch("judgment_app.read_judgment_web.get_judgment_web")
    @patch("judgment_app.read_judgment_web.get_judgment_pdf")
    @patch("judgment_app.read_judgment_web.search_judgments")
    def test_api_only_searches_jid_and_saves_json(self, search, pdf, web, token, api):
        jid = "TPDM,114,訴,218,20250328,1"
        search.return_value = [jid]
        document = {"jid": jid, "JFULLX": {"JFULLCONTENT": "判決全文"}}
        api.return_value = document
        messages = []
        with TemporaryDirectory() as folder:
            case = CaseNumber(court="TPD", year=114, case="訴", number=218)
            report = download_cases([case], folder, api=True, on_result=messages.append)
            self.assertFalse(report["errors"])
            self.assertEqual(len(report["files"]), 1)
            path = Path(report["files"][0])
            self.assertEqual(path.name, "臺灣臺北地方法院114年度訴字第218號.json")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), document)
            api.assert_called_once_with("test-token", jid)
            token.assert_called_once()
            search.assert_called_once()
            self.assertEqual(search.call_args.args[0].court, "TPD")
            web.assert_not_called()
            pdf.assert_not_called()
            self.assertTrue(any("已取得 JID" in text for text in messages))
            self.assertTrue(any("下載成功" in text for text in messages))

    @patch("judgment_app.download_judgments.get_token")
    @patch("judgment_app.read_judgment_web.get_judgment_web")
    @patch("judgment_app.read_judgment_web.get_judgment_pdf", return_value=b"%PDF-test")
    @patch("judgment_app.read_judgment_web.search_judgments", return_value=["TPDM,114,訴,218,20250328,1"])
    def test_existing_formats_skip_before_download(self, search, pdf, web, token):
        with TemporaryDirectory() as folder:
            name = "臺灣臺北地方法院114年度訴字第218號"
            for suffix in ('.txt', '.json'):
                (Path(folder) / (name + suffix)).write_bytes(b'existing')
            messages = []
            report = download_cases([CaseNumber(court='TPD', year=114, case='訴', number=218)], folder,
                                    web=True, pdf=True, api=True, on_result=messages.append)
            self.assertEqual(len(report['skipped']), 2)
            self.assertEqual(len(report['files']), 1)
            web.assert_not_called()
            token.assert_not_called()
            pdf.assert_called_once()
            self.assertEqual(sum('已存在' in text for text in messages), 2)
            self.assertEqual((Path(folder) / (name + '.txt')).read_bytes(), b'existing')

    def test_pdf_rejects_html_error_response(self):
        session = Mock()
        session.get.side_effect = [
            self.response('<a id="hlExportPDF" href="/FILES/test.pdf">PDF</a>'),
            self.response('<html>Error</html>'),
        ]
        with self.assertRaisesRegex(RuntimeError, "不是 PDF"):
            get_judgment_pdf("TPDM,114,訴,218,20250328,1", session=session)

    def test_existing_file_is_not_overwritten(self):
        with TemporaryDirectory() as folder:
            first = save_document(folder, "案號", ".txt", b"first")
            second = save_document(folder, "案號", ".txt", b"second")
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b"first")

    @patch("judgment_app.download_judgments.get_judgment", return_value={"content": "api"})
    @patch("judgment_app.download_judgments.get_token", return_value="test")
    @patch("judgment_app.read_judgment_web.get_judgment_pdf", return_value=b"%PDF-test")
    @patch("judgment_app.read_judgment_web.get_judgment_web", return_value={"text": "正文"})
    @patch("judgment_app.read_judgment_web.search_judgments", return_value=["TPDM,114,訴,218,20250328,1", "TPDM,114,訴,218,20250428,1"])
    def test_all_modes_search_once_and_number_documents(self, search, web, pdf, token, api):
        with TemporaryDirectory() as folder:
            report = download_cases([CaseNumber(court="TPD", year=114, case="訴", number=218)], folder, web=True, pdf=True, api=True)
            self.assertEqual(len(report["files"]), 6)
            self.assertFalse(report["errors"])
            self.assertTrue((Path(folder) / "臺灣臺北地方法院114年度訴字第218號_2.pdf").exists())
            search.assert_called_once()
            token.assert_called_once()

    def test_extracts_body_only_and_keeps_callers_session(self):
        session = Mock()
        session.get.return_value = self.response(
            '<meta charset="utf-8"><nav>導覽</nav>'
            '<div class="jud_content">判決<br>主文<script>noise</script></div>'
        )
        result = get_judgment_web("TPDM,114,訴,218,20250328,1", session=session)
        self.assertEqual(result["text"], "判決\n主文")
        session.close.assert_not_called()

    def test_does_not_save_error_page_as_judgment(self):
        session = Mock()
        session.get.return_value = self.response("<html>Access denied</html>")
        with self.assertRaisesRegex(RuntimeError, "找不到裁判正文"):
            get_judgment_web("TPDM,114,訴,218,20250328,1", session=session)

    def test_records_and_stops_on_rate_limit(self):
        records = []
        with MeasuredSession(records) as session:
            response = self.response("limited", 429)
            response.headers["Retry-After"] = "60"
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                session.record_response(response)
        self.assertEqual(records[0]["status"], 429)
        self.assertEqual(records[0]["retry_after"], "60")

    def test_withheld_results_are_not_a_network_failure(self):
        session = Mock()
        session.get.side_effect = [
            self.response('<form><input type="hidden" name="__VIEWSTATE" value="x"></form>'),
            self.response('<meta charset="utf-8">本件經程式判定為依法不得公開或須去識別化後公開之案件'),
        ]
        session.post.return_value = self.response(
            '<iframe id="iframe-data" src="qryresultlst.aspx?ty=JUDBOOK&amp;q=test"></iframe>'
        )
        self.assertEqual(case_to_jids(CaseNumber(court="TPHM", year=114, case="國金上重訴", number=1), session=session), [])
        self.assertEqual(session.post.call_args.kwargs["data"]["jud_court"], "TPH")
        self.assertEqual(session.post.call_args.kwargs["data"]["jud_sys"], "M")

    @patch("judgment_app.read_judgment_web.read_case_numbers")
    @patch("judgment_app.read_judgment_web.search_judgments", return_value=[])
    def test_batch_serializes_case_number(self, search, read):
        case = CaseNumber("TPD", 114, "訴", 219)
        read.return_value = [case]
        with TemporaryDirectory() as folder:
            output = Path(folder) / "report"
            report = run_batch(Path(folder) / "cases.xlsx", output)
            saved = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertIsNone(report["error"])
            self.assertEqual(saved["completed_cases"], 1)
            self.assertEqual(saved["cases"][0]["case"],
                             {"court": "TPD", "year": 114, "case": "訴", "number": 219})
            self.assertEqual(search.call_args.args, (case,))

    @patch("judgment_app.read_judgment_web.search_judgments", return_value=["TPDM,114,訴,219,20250328,1"])
    @patch("judgment_app.read_judgment_web.get_judgment_pdf", return_value=b"%PDF-test")
    def test_download_case_pdfs_accepts_case_number(self, pdf, search):
        case = CaseNumber("TPD", 114, "訴", 219)
        with TemporaryDirectory() as folder:
            paths = download_case_pdfs(case, folder)
            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0].read_bytes(), b"%PDF-test")
            self.assertEqual(search.call_args.args, (case,))

    def response(self, html, status=200):
        response = requests.Response()
        response.status_code = status
        response.url = "https://judgment.judicial.gov.tw/FJUD/data.aspx"
        response._content = html.encode("utf-8")
        response.request = requests.Request("GET", response.url).prepare()
        return response


if __name__ == "__main__":
    unittest.main()
