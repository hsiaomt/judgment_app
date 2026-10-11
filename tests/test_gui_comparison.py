import json
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from judgment_app.gui.compare_page import ComparePage


class GuiComparisonTests(unittest.TestCase):
    def app(self):
        app = ComparePage.__new__(ComparePage)
        app.busy = False
        app.root = Mock()
        app.comparison_file = Mock()
        app.json_source = Mock()
        app.output = Mock()
        app.status = Mock()
        app.events = Queue()
        app.run_task = Mock()
        app.append_log = Mock()
        app.set_busy = Mock()
        app.schedule_poll = Mock()
        app.comparison_tree = Mock()
        app.comparison_tree.get_children.return_value = ["old"]
        app.comparison_tree.insert.side_effect = [str(i) for i in range(10)]
        app.comparison_rows = {"old": {}}
        app.comparison_summary = Mock()
        app.result_tabs = Mock()
        app.comparison_frame = Mock()
        return app

    def test_background_comparison_saves_full_report_and_displays_only_non_equal(self):
        app = self.app()
        report = {
            'rows': [{'row': 2, 'status': 'equal', 'name': 'equal-person'},
                     {'row': 3, 'status': 'different', 'differences': [{'column': '累犯'}]},
                     {'row': 4, 'status': 'unmatched', 'reason': '判決不存在'},
                     {'row': 5, 'status': 'invalid', 'reason': '格式錯誤'},
                     {'row': 6, 'status': 'incomplete', 'uncomparable': []}],
            'summary': {'total': 5}, 'errors': [{'error': '讀取失敗'}], 'sheets': [],
        }
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            excel = folder / 'source.xlsx'
            excel.touch()
            app.comparison_file.get.return_value = str(excel)
            app.json_source.get.return_value = directory
            app.output.get.return_value = str(folder / "comparison_report.json")
            app.start_comparison()
            kind, operation = app.run_task.call_args.args
            self.assertEqual(kind, 'comparison')
            with patch('judgment_app.analysis.compare.compare_files', return_value=report) as compare:
                result = operation()
            self.assertEqual(compare.call_args.args, (excel, folder))
            self.assertEqual(json.loads((folder / 'comparison_report.json').read_text()), report)
            app.events.put((kind, result, None))
            app.poll()
        log = '\n'.join(call.args[0] for call in app.append_log.call_args_list)
        self.assertNotIn('equal-person', log)
        values = [call.kwargs['values'] for call in app.comparison_tree.insert.call_args_list]
        self.assertEqual([row[5] for row in values], ['different', 'unmatched', 'invalid', 'incomplete', 'error'])
        self.assertIn('累犯', values[0][6])
        self.assertEqual(values[-1][6], '讀取失敗')
        self.assertNotIn('old', app.comparison_rows)
        app.result_tabs.select.assert_called_once_with(app.comparison_frame)
        self.assertFalse(app.set_busy.call_args.args[0])

    def test_busy_and_missing_selection_do_not_start(self):
        app = self.app()
        app.busy = True
        app.start_comparison()
        app.busy = False
        app.comparison_file.get.return_value = ''
        app.output.get.return_value = ''
        with patch('judgment_app.gui.compare_page.messagebox.showerror') as error:
            app.start_comparison()
            error.assert_called_once()
        app.run_task.assert_not_called()

    def test_comparison_failure_restores_controls(self):
        app = self.app()
        app.events.put(('comparison', None, OSError('無法寫入報告')))
        with patch('judgment_app.gui.compare_page.messagebox.showerror') as error:
            app.poll()
            error.assert_called_once()
        app.append_log.assert_called_once_with('OSError: 無法寫入報告')
        self.assertFalse(app.set_busy.call_args.args[0])

    def test_cancel_keeps_previous_selection(self):
        app = self.app()
        with patch('judgment_app.gui.common.filedialog.askopenfilename', return_value=''):
            app.choose_path(app.comparison_file, "file")
        app.comparison_file.set.assert_not_called()

    def test_import_existing_report_without_comparing_or_writing(self):
        app = self.app()
        report = {'rows': [{'status': 'equal'}, {'status': 'different',
                  'differences': [{'column': '累犯', 'excel_value': True, 'json_value': False}]}],
                  'errors': [{'error': '來源讀取錯誤'}], 'summary': {'total': 999}}
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'report.json'
            source.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8-sig')
            original = source.read_bytes()
            with patch('judgment_app.gui.compare_page.filedialog.askopenfilename', return_value=str(source)), patch('judgment_app.gui.compare_page.compare_to_report') as compare:
                app.import_report()
                kind, operation = app.run_task.call_args.args
                self.assertEqual(kind, 'import_report')
                app.events.put((kind, operation(), None))
                app.poll()
                compare.assert_not_called()
            self.assertEqual(source.read_bytes(), original)
        self.assertIn('報告匯入完成：共 2 列，equal 1 列', app.status.set.call_args.args[0])
        values = [call.kwargs['values'] for call in app.comparison_tree.insert.call_args_list]
        self.assertEqual([row[5] for row in values], ['different', 'error'])
        app.output.set.assert_not_called()

    def test_import_cancel_and_busy_do_not_start(self):
        app = self.app()
        with patch('judgment_app.gui.compare_page.filedialog.askopenfilename', return_value='') as choose:
            app.import_report()
            choose.assert_called_once()
            app.busy = True
            app.import_report()
            choose.assert_called_once()
        app.run_task.assert_not_called()

    def test_invalid_import_preserves_table_and_restores_controls(self):
        from judgment_app.analysis.compare import load_comparison_report
        invalid = ['{', '[]', '{"case_number": "判決分析"}',
                   '{"rows": [{"status": "different", "differences": [null]}], "errors": []}',
                   '{"rows": [{"status": "invalid", "reason": {}}], "errors": []}',
                   '{"rows": [], "errors": ["bad"]}']
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'report.json'
            for content in invalid:
                with self.subTest(content=content):
                    app = self.app()
                    source.write_text(content)
                    try:
                        load_comparison_report(source)
                    except ValueError as exc:
                        app.events.put(('import_report', None, exc))
                    else:
                        self.fail('Invalid report was accepted')
                    with patch('judgment_app.gui.common.messagebox.showerror') as error:
                        app.poll()
                        error.assert_called_once()
                    app.comparison_tree.delete.assert_not_called()
                    self.assertFalse(app.set_busy.call_args.args[0])

    def test_empty_report_clears_previous_results(self):
        from judgment_app.analysis.compare import load_comparison_report
        app = self.app()
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'report.json'
            source.write_text('{"rows": [], "errors": []}')
            app.task_completed('import_report', (load_comparison_report(source), source))
        app.comparison_tree.delete.assert_called_once_with('old')
        self.assertEqual(app.comparison_rows, {})
        self.assertIn('共 0 列', app.status.set.call_args.args[0])
