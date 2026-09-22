from dataclasses import replace
import tkinter as tk
import unittest
from unittest.mock import patch

from judgment_app.case_number import CaseNumber
from judgment_app.gui import CaseReaderApp


class CaseListTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = CaseReaderApp(self.root)

    def tearDown(self):
        for callback in self.root.tk.call('after', 'info'):
            self.root.after_cancel(callback)
        self.root.destroy()

    def test_history_summary_and_failure_annotation(self):
        original = CaseNumber(court='TPD', year=114, case='訴', number=218)
        failed = CaseNumber(court='TPH', year=114, case='上訴', number=3070, original_case=original)
        self.app.events.put(('download', dict(
            files=['original.txt'], history_files=['history.txt'],
            skipped=[], history_skipped=[], empty=[], errors=['失敗原因'], failed_cases=[failed],
        ), None))
        self.app.poll()
        text = self.app.log.get('1.0', 'end')
        self.assertIn('原始判決 1 個檔案；歷審判決 1 個檔案', text)
        self.assertIn('臺灣高等法院 114年度上訴字第3070號（原始案號：臺灣臺北地方法院 114年度訴字第218號）', text)

    def test_multiple_sort_directions_and_bulk_delete(self):
        app = self.app
        app.merge_cases([
            CaseNumber(court='TPD', year=year, case='訴', number=number)
            for year, number in [(114, 10), (113, 2), (114, 2), (113, 10)]
        ])
        app.sort_rules = [('year', False), ('number', True)]
        app.refresh_cases()
        self.assertEqual([(c.year, c.number) for c in app.visible_cases()], [(113, 10), (113, 2), (114, 10), (114, 2)])
        items = app.tree.get_children()
        app.tree.selection_set(items[0], items[2])
        app.delete_case()
        self.assertEqual([(c.year, c.number) for c in app.visible_cases()], [(113, 2), (114, 2)])
        app.tree.selection_set(*app.tree.get_children())
        app.delete_case()
        self.assertEqual(app.cases, [])
        self.assertEqual(str(app.download_button['state']), 'disabled')

    def test_manual_import_sort_delete_and_download_snapshot(self):
        app = self.app
        app.manual_year.set('114')
        app.manual_case.set('訴')
        app.manual_number.set('10')
        app.add_case()
        first = CaseNumber(court='TPD', year=114, case='訴', number=10)
        second = replace(first, number=2)
        app.start_row.set('5')
        self.assertEqual(app.visible_cases(), [first])
        app.merge_cases([first, second])
        self.assertEqual(len(app.visible_cases()), 2)
        app.manual_number.set('30')
        app.add_case()
        app.add_case()  # 重複不新增
        self.assertEqual(len(app.visible_cases()), 3)
        app.sort_cases('number')
        self.assertEqual([c.number for c in app.visible_cases()], [2, 10, 30])
        app.sort_cases('number')
        self.assertEqual([c.number for c in app.visible_cases()], [30, 10, 2])
        app.tree.selection_set(app.tree.get_children()[1])
        app.delete_case()
        expected = app.visible_cases()
        self.assertEqual([c.number for c in expected], [30, 2])
        app.history.set(True)
        with patch.object(app, 'run_task', side_effect=lambda kind, operation: operation()), patch('judgment_app.gui.download_cases') as download:
            app.start_download()
            self.assertEqual(download.call_args.args[0], expected)
            self.assertTrue(download.call_args.kwargs["history"])
        for item in list(app.tree.get_children()):
            app.tree.selection_set(item)
            app.delete_case()
            break
        app.tree.selection_set(app.tree.get_children()[0])
        app.delete_case()
        self.assertEqual(app.visible_cases(), [])
        self.assertEqual(str(app.download_button['state']), 'disabled')


if __name__ == '__main__':
    unittest.main()
