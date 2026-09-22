import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests

from judgment_app.case_number import CaseNumber
from judgment_app.read_judgment_web import download_cases, get_judgment_history, case_label


ORIGINAL = 'TPDM,114,訴,218,20250328,1'
RELATED = 'TPHM,114,上訴,3070,20250910,1'
CASE = CaseNumber(court='TPD', year=114, case='訴', number=218)


def response(text):
    result = requests.Response()
    result.status_code = 200
    result.url = 'https://judgment.judicial.gov.tw/FJUD/data.aspx'
    result._content = text.encode('utf-8')
    return result


class HistoryTests(unittest.TestCase):
    def test_ajax_filters_orders_self_duplicates_and_unlinked_cases(self):
        session = Mock()
        session.get.side_effect = [response('''
            <meta charset="utf-8"><div class="rela-area col-xs-4"><div id="JudHis"><ul></ul></div></div>
            <script>$.ajax({url: "../controls/GetJudHistory.ashx?jid=test"})</script>
        '''), response(json.dumps({'count': 6, 'list': [
            dict(desc='原始判決(114.03.28)', href=f'data.aspx?ty=JD&id={ORIGINAL}'),
            dict(desc='上訴判決(114.09.10)', href=f'data.aspx?ty=JD&id={RELATED}'),
            dict(desc='上訴判決(114.09.10)', href=f'data.aspx?ty=JD&id={RELATED}'),
            dict(desc='裁定(114.09.11)', href='data.aspx?ty=JD&id=TPHM,114,上訴,3070,20250911,1'),
            dict(desc='尚無裁判', href=''),
            dict(desc='判決', href=''),
        ]}))]
        self.assertEqual(get_judgment_history(ORIGINAL, session=session), [RELATED])
        self.assertEqual(session.get.call_args.args[0], 'https://judgment.judicial.gov.tw/controls/GetJudHistory.ashx?jid=test')

    def test_static_panel_does_not_include_other_related_sections(self):
        session = Mock()
        session.get.return_value = response(f'''<meta charset="utf-8">
            <div class="rela-area"><div id="JudHis"><ul>
            <li><a href="data.aspx?ty=JD&amp;id={RELATED}">歷審判決</a></li>
            </ul></div><a href="data.aspx?ty=JD&amp;id=OTHER,114,訴,1,20250101,1">其他判決</a></div>''')
        self.assertEqual(get_judgment_history(ORIGINAL, session=session), [RELATED])

    @patch('judgment_app.read_judgment_web.search_judgments', return_value=[ORIGINAL])
    @patch('judgment_app.read_judgment_web.get_judgment_history', return_value=[ORIGINAL, RELATED, RELATED])
    @patch('judgment_app.read_judgment_web.get_judgment_web', return_value={'text': '判決正文'})
    def test_grouping_counts_and_rerun_with_existing_original(self, web, history, search):
        with TemporaryDirectory() as folder:
            report = download_cases([CASE], folder, web=True, history=True)
            self.assertEqual(len(report['files']), 1)
            self.assertEqual(len(report['history_files']), 1)
            for file in report['files'] + report['history_files']:
                self.assertEqual(Path(file).parent, Path(folder) / case_label(CASE))
            self.assertEqual(web.call_count, 2)
            web.reset_mock()
            report = download_cases([CASE], folder, web=True, history=True)
            self.assertEqual(len(report['skipped']), 1)
            self.assertEqual(len(report['history_skipped']), 1)
            self.assertFalse(report['history_files'])
            web.assert_not_called()
            self.assertEqual(history.call_count, 2)

    @patch('judgment_app.read_judgment_web.search_judgments', return_value=[ORIGINAL])
    @patch('judgment_app.read_judgment_web.get_judgment_history', return_value=[RELATED])
    @patch('judgment_app.read_judgment_web.get_judgment_web', side_effect=[{'text': '正文'}, RuntimeError('失敗')])
    def test_history_failure_identifies_related_case_and_original(self, web, history, search):
        with TemporaryDirectory() as folder:
            messages = []
            report = download_cases([CASE], folder, web=True, history=True, on_result=messages.append)
            self.assertEqual(len(report['files']), 1)
            self.assertFalse(report['history_files'])
            failed = report['failed_cases'][0]
            self.assertEqual(failed.court, 'TPH')
            self.assertEqual(failed.number, 3070)
            self.assertEqual(failed.original_case, CASE)
            self.assertTrue(any('歷審下載失敗' in m and '原始案號' in m for m in messages))

    def test_selected_formats_and_json_does_not_trigger_history(self):
        for web, pdf, api in [(True, False, False), (False, True, False),
                              (True, True, True), (False, False, True)]:
            with self.subTest(web=web, pdf=pdf, api=api), TemporaryDirectory() as folder, \
                    patch('judgment_app.read_judgment_web.search_judgments', return_value=[ORIGINAL]), \
                    patch('judgment_app.read_judgment_web.get_judgment_history', return_value=[RELATED]) as history, \
                    patch('judgment_app.read_judgment_web.get_judgment_web', return_value={'text': '正文'}) as get_web, \
                    patch('judgment_app.read_judgment_web.get_judgment_pdf', return_value=b'%PDF-test') as get_pdf, \
                    patch('judgment_app.download_judgments.get_token', return_value='token'), \
                    patch('judgment_app.download_judgments.get_judgment', return_value={}) as get_api:
                report = download_cases([CASE], folder, web=web, pdf=pdf, api=api, history=True)
                expected = ({'.txt'} if web else set()) | ({'.pdf'} if pdf else set())
                self.assertEqual({Path(f).suffix for f in report['history_files']}, expected)
                self.assertEqual(get_web.call_count, 2 if web else 0)
                self.assertEqual(get_pdf.call_count, 2 if pdf else 0)
                self.assertEqual(get_api.call_count, 1 if api else 0)
                self.assertEqual(history.call_count, 1 if expected else 0)
                if not expected:
                    self.assertEqual(Path(report['files'][0]).parent, Path(folder))
                report = download_cases([CASE], folder, web=web, pdf=pdf, api=api, history=True)
                self.assertFalse(report['history_files'])
                self.assertEqual(len(report['history_skipped']), len(expected))

    @patch('judgment_app.read_judgment_web.search_judgments', return_value=[ORIGINAL])
    @patch('judgment_app.read_judgment_web.get_judgment_history', return_value=[RELATED])
    @patch('judgment_app.read_judgment_web.get_judgment_web', side_effect=[{'text': '正文'}, RuntimeError('失敗')])
    @patch('judgment_app.read_judgment_web.get_judgment_pdf', return_value=b'%PDF-test')
    def test_history_txt_failure_still_downloads_pdf(self, pdf, web, history, search):
        with TemporaryDirectory() as folder:
            report = download_cases([CASE], folder, web=True, pdf=True, history=True)
            self.assertEqual([Path(f).suffix for f in report['history_files']], ['.pdf'])
            self.assertEqual(len(report['errors']), 1)
            self.assertIn('[網頁全文]', report['errors'][0])
            self.assertEqual(report['failed_cases'][0].original_case, CASE)

    def test_missing_history_loader_is_error(self):
        session = Mock()
        session.get.return_value = response('<div class="rela-area"><div id="JudHis"><ul></ul></div></div>')
        with self.assertRaisesRegex(RuntimeError, '載入連結'):
            get_judgment_history(ORIGINAL, session=session)
