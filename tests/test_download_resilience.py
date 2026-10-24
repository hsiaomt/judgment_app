import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests

from judgment_app.acquisition.web import (
    case_to_jids, download_cases, download_case_pdfs, get_judgment_history,
    get_judgment_web, run_batch, save_document,
)
from judgment_app.case_number import JudgmentId
from judgment_app.exceptions import ApiResponseError


FIRST = JudgmentId.from_string("TPDM,115,易,230,20260730,1")
SECOND = JudgmentId.from_string("TPDM,115,易,231,20260730,1")
RELATED = JudgmentId.from_string("TPHM,115,上訴,230,20260830,1")
RESULT_URL = "https://judgment.judicial.gov.tw/FJUD/qryresultlst.aspx?ty=JUDBOOK&q=test"


def response(html, status=200):
    result = requests.Response()
    result.status_code = status
    result.url = RESULT_URL
    result._content = (html if html.startswith("{") else '<meta charset="utf-8">' + html).encode("utf-8")
    result.headers["Content-Type"] = "text/html; charset=utf-8"
    result.encoding = "utf-8"
    result._content_consumed = True
    return result


def link(jid=FIRST):
    return f'<a href="data.aspx?ty=JD&amp;id={jid.to_string()}">刑事判決</a>'


def search_session(*pages):
    session = Mock()
    session.get.side_effect = [response('<form action="Default_AD.aspx"></form>'), *pages]
    session.post.return_value = response('<iframe id="iframe-data" src="qryresultlst.aspx?ty=JUDBOOK&amp;q=test"></iframe>')
    return session


class SearchResilienceTests(unittest.TestCase):
    def test_actual_withheld_page_shape_is_empty_not_error(self):
        # 2026-10-24 使用者提供頁面的結構：hlTitle 沒有 href，摘要說明遮隱全文。
        page = response('''<table id="jud"><tr><td>
            <a id="hlTitle" class="hlTitle_scroll">刑事判決</a>（0K）</td></tr>
            <tr class="summary"><td><span class="tdCut">為保障被害人權益，
            致須遮隱本件裁判書全文。</span></td></tr></table>''')
        self.assertEqual(case_to_jids(FIRST.case_number, session=search_session(page)), [])

    def test_unrecognized_empty_and_block_pages_remain_failures(self):
        for html in ("", "Access denied", '<table id="jud"><a id="hlTitle">刑事判決</a></table>'):
            with self.subTest(html=html), self.assertRaisesRegex(RuntimeError, "沒有裁判連結"):
                case_to_jids(FIRST.case_number, session=search_session(response(html)))

    def test_withheld_page_still_follows_next_page(self):
        page = response('本件經程式判定為依法不得公開或須去識別化後公開之案件'
                        '<a href="qryresultlst.aspx?page=2">下一頁</a>')
        result = case_to_jids(FIRST.case_number, session=search_session(page, response(link())))
        self.assertEqual(result, [FIRST])

    def test_valid_links_survive_malformed_jid_and_later_page_failure(self):
        page = response(link() + '<a href="data.aspx?ty=JD&amp;id=invalid">判決</a>'
                        '<a href="qryresultlst.aspx?page=2">下一頁</a>')
        errors = []
        result = case_to_jids(FIRST.case_number, session=search_session(page, response("error", 403)),
                              on_error=errors.append)
        self.assertEqual(result, [FIRST])
        self.assertEqual(len(errors), 2)
        self.assertIsInstance(errors[0], ValueError)
        self.assertIsInstance(errors[1], requests.HTTPError)

    def test_pagination_cycle_and_page_limit_preserve_downloadable_jids(self):
        for href, limit in ((RESULT_URL, 100), ("qryresultlst.aspx?page=2", 1)):
            with self.subTest(href=href), patch("judgment_app.acquisition.web.MAX_RESULT_PAGES", limit):
                errors = []
                page = response(link() + f'<a href="{href}">下一頁</a>')
                self.assertEqual(case_to_jids(FIRST.case_number, session=search_session(page),
                                             on_error=errors.append), [FIRST])
                self.assertEqual(len(errors), 1)


class DownloadResilienceTests(unittest.TestCase):
    def test_search_failure_does_not_stop_next_case_or_become_empty(self):
        for error in (RuntimeError("結果頁沒有裁判連結"), requests.Timeout("timeout"),
                      ValueError("invalid JID"), requests.TooManyRedirects("redirect")):
            with self.subTest(error=error), TemporaryDirectory() as folder, \
                    patch("judgment_app.acquisition.web.case_to_jids", side_effect=[error, [SECOND]]), \
                    patch("judgment_app.acquisition.web.get_judgment_web", return_value={"text": "正文"}):
                messages = []
                report = download_cases([FIRST.case_number, SECOND.case_number], folder, web=True,
                                        on_result=messages.append)
                self.assertEqual(len(report["files"]), 1)
                self.assertEqual(report["failed_cases"], [FIRST.case_number])
                self.assertFalse(report["empty"])
                self.assertIn(type(error).__name__, report["errors"][0])
                self.assertIn(report["errors"][0], messages)

    def test_each_format_and_later_documents_continue_after_http_or_disk_error(self):
        for error in (requests.HTTPError(response=response("denied", 403)),
                      requests.HTTPError(response=response("limited", 429)),
                      requests.HTTPError(response=response("maintenance", 503)),
                      PermissionError("read-only"), OSError("disk full"),
                      requests.exceptions.SSLError("certificate")):
            with self.subTest(error=error), TemporaryDirectory() as folder, \
                    patch("judgment_app.acquisition.web.case_to_jids", return_value=[FIRST, SECOND]), \
                    patch("judgment_app.acquisition.web.get_judgment_web", side_effect=[error, {"text": "正文"}]), \
                    patch("judgment_app.acquisition.web.get_judgment_pdf", return_value=b"%PDF-test"):
                report = download_cases([FIRST.case_number], folder, web=True, pdf=True)
                self.assertEqual(len(report["files"]), 3)
                self.assertEqual(len(report["errors"]), 1)
                self.assertEqual(report["failed_cases"], [FIRST.case_number])

    def test_partial_search_errors_still_download_found_jids(self):
        def search(case, *, session, on_error):
            on_error(RuntimeError("第二頁失敗"))
            return [FIRST]

        with TemporaryDirectory() as folder, \
                patch("judgment_app.acquisition.web.case_to_jids", side_effect=search), \
                patch("judgment_app.acquisition.web.get_judgment_web", return_value={"text": "正文"}):
            report = download_cases([FIRST.case_number], folder, web=True)
            self.assertEqual(len(report["files"]), 1)
            self.assertEqual(report["failed_cases"], [FIRST.case_number])
            self.assertFalse(report["empty"])

    def test_api_passes_string_jid_and_api_failure_keeps_other_downloads(self):
        for error in (ApiResponseError("API unavailable"), ValueError("invalid JSON"), RuntimeError("missing credentials")):
            with self.subTest(error=error), TemporaryDirectory() as folder, \
                    patch("judgment_app.acquisition.web.case_to_jids", side_effect=[[FIRST], [SECOND]]), \
                    patch("judgment_app.acquisition.web.get_judgment_web", return_value={"text": "正文"}), \
                    patch("judgment_app.acquisition.api.get_token", return_value="token"), \
                    patch("judgment_app.acquisition.api.get_judgment", side_effect=[error, {"text": "api"}]) as api:
                report = download_cases([FIRST.case_number, SECOND.case_number], folder, web=True, api=True)
                self.assertEqual(len(report["files"]), 3)
                self.assertEqual(len(report["errors"]), 1)
                self.assertEqual([call.args[1] for call in api.call_args_list], [FIRST.to_string(), SECOND.to_string()])

    def test_history_lookup_and_download_errors_continue_to_next_case(self):
        for lookup_fails in (False, True):
            with self.subTest(lookup_fails=lookup_fails), TemporaryDirectory() as folder, \
                    patch("judgment_app.acquisition.web.case_to_jids", side_effect=[[FIRST], [SECOND]]), \
                    patch("judgment_app.acquisition.web.get_judgment_history",
                          side_effect=[requests.HTTPError(response=response("denied", 403)) if lookup_fails else [RELATED], []]), \
                    patch("judgment_app.acquisition.web.get_judgment_web",
                          side_effect=[{"text": "first"}, {"text": "second"}] if lookup_fails else
                          [{"text": "first"}, PermissionError("disk error"), {"text": "second"}]):
                report = download_cases([FIRST.case_number, SECOND.case_number], folder, web=True, history=True)
                self.assertEqual(len(report["files"]), 2)
                self.assertEqual(len(report["errors"]), 1)
                self.assertIn("歷審", report["errors"][0])

    def test_batch_continues_and_serializes_nonempty_jids(self):
        with TemporaryDirectory() as folder, \
                patch("judgment_app.acquisition.web.read_case_numbers", return_value=[FIRST.case_number, SECOND.case_number]), \
                patch("judgment_app.acquisition.web.case_to_jids", side_effect=[[FIRST, RELATED], [SECOND]]), \
                patch("judgment_app.acquisition.web.get_judgment_web", side_effect=[RuntimeError("failed"), {"text": "related"}, {"text": "second"}]):
            output = Path(folder) / "batch"
            report = run_batch(Path(folder) / "cases.xlsx", output)
            saved = json.loads((output / "report.json").read_text())
            self.assertEqual(report["documents"], 2)
            self.assertEqual(report["completed_cases"], 1)
            self.assertEqual(saved["cases"][0]["jids"], [FIRST.to_string(), RELATED.to_string()])
            self.assertTrue(saved["cases"][0]["errors"])

    def test_pdf_batch_keeps_success_after_single_failure(self):
        with TemporaryDirectory() as folder, \
                patch("judgment_app.acquisition.web.case_to_jids", return_value=[FIRST, SECOND]), \
                patch("judgment_app.acquisition.web.get_judgment_pdf", side_effect=[RuntimeError("failed"), b"%PDF-test"]):
            messages = []
            paths = download_case_pdfs(FIRST.case_number, folder, on_result=messages.append)
            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0].read_bytes(), b"%PDF-test")
            self.assertIn(FIRST.to_string(), messages[0])

    def test_failed_write_removes_partial_file_and_allows_retry(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / "judgment.txt"
            original_open = Path.open

            def fail_write(path, *args, **kwargs):
                file = original_open(path, *args, **kwargs)
                file.write(b"partial")
                wrapped = Mock(wraps=file)
                wrapped.write.side_effect = OSError("disk full")
                context = Mock()
                context.__enter__ = Mock(return_value=wrapped)
                context.__exit__ = Mock(side_effect=lambda *args: file.close())
                return context

            with patch.object(Path, "open", fail_write), self.assertRaisesRegex(OSError, "disk full"):
                save_document(folder, "judgment", ".txt", b"complete")
            self.assertFalse(target.exists())
            self.assertEqual(save_document(folder, "judgment", ".txt", b"complete"), target)
            self.assertEqual(target.read_bytes(), b"complete")


class RequestResilienceTests(unittest.TestCase):
    @patch("judgment_app.acquisition.web.time.sleep")
    def test_transient_failure_retries_then_succeeds(self, sleep):
        for failure in (requests.Timeout("slow"), requests.ConnectionError("offline"),
                        requests.exceptions.ChunkedEncodingError("truncated"), response("unavailable", 503)):
            with self.subTest(failure=failure):
                session = Mock()
                session.get.side_effect = [failure, response('<div class="jud_content">正文</div>')]
                self.assertEqual(get_judgment_web(FIRST, session=session)["text"], "正文")
                self.assertEqual(session.get.call_count, 2)
                self.assertEqual(session.get.call_args.kwargs["timeout"], (10, 30))

    @patch("judgment_app.acquisition.web.time.sleep")
    def test_retries_exhaust_after_three_attempts(self, sleep):
        session = Mock()
        session.get.side_effect = requests.Timeout("slow")
        with self.assertRaises(requests.Timeout):
            get_judgment_web(FIRST, session=session)
        self.assertEqual(session.get.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    @patch("judgment_app.acquisition.web.time.sleep")
    def test_denial_limits_ssl_and_retry_after_do_not_retry(self, sleep):
        maintenance = response("maintenance", 503)
        maintenance.headers["Retry-After"] = "3600"
        for failure in (response("denied", 403), response("limited", 429), maintenance,
                        requests.exceptions.SSLError("certificate")):
            with self.subTest(failure=failure):
                session = Mock()
                session.get.side_effect = [failure]
                with self.assertRaises(requests.RequestException):
                    get_judgment_web(FIRST, session=session)
                self.assertEqual(session.get.call_count, 1)
        sleep.assert_not_called()

    def test_bad_history_entry_does_not_drop_valid_entries(self):
        session = Mock()
        session.get.side_effect = [response('''<div class="rela-area"><div id="JudHis"></div></div>
            <script>$.get('../controls/GetJudHistory.ashx?jid=test')</script>'''),
            response(json.dumps({"list": [{"desc": None, "href": []},
                                          {"desc": "判決", "href": f"data.aspx?ty=JD&id={RELATED.to_string()}"}]}))]
        errors = []
        self.assertEqual(get_judgment_history(FIRST, session=session, on_error=errors.append), [RELATED])
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
