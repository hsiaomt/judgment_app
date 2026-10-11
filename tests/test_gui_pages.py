"""真實 Tk 分頁與背景佇列驗證；外部下載／LLM 呼叫以測試替身隔離。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, get_ident
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import pandas as pd

from judgment_app.gui import JudgmentApp


class PageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = JudgmentApp(self.root)
        self.worker_release = Event()

    def tearDown(self):
        self.worker_release.set()
        for callback in self.root.tk.call('after', 'info'):
            self.root.after_cancel(callback)
        self.root.destroy()

    def wait_until(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)
        self.assertTrue(condition())

    def test_module_and_launcher_keep_main_entry(self):
        import runpy
        import launcher
        from judgment_app.gui import main
        self.assertIs(launcher.main, main)
        with patch('judgment_app.gui.app.main') as entry:
            runpy.run_module('judgment_app.gui', run_name='__main__')
            entry.assert_called_once_with()

    def test_independent_pages_and_background_completion_on_main_thread(self):
        pages = (self.app.download_page, self.app.analysis_page, self.app.compare_page)
        self.assertEqual([self.app.notebook.tab(tab, 'text') for tab in self.app.notebook.tabs()],
                         ['下載判決書', '分析判決書', '結果比對'])
        for page in pages:
            self.app.notebook.select(page)
            self.root.update()
            self.assertEqual(self.app.notebook.select(), str(page))
        page = self.app.analysis_page
        entered = Event()
        worker_threads, completion_threads, heartbeats = [], [], []
        original = page.task_completed

        def operation():
            worker_threads.append(get_ident())
            entered.set()
            self.worker_release.wait(3)
            page.progress('分析進度')
            return {'total': 0, 'files': [], 'outputs': [], 'errors': []}

        def completed(*args):
            completion_threads.append(get_ident())
            return original(*args)

        with patch.object(page, 'task_completed', side_effect=completed):
            page.run_task('analysis', operation)
            self.assertTrue(entered.wait(1))
            self.assertTrue(page.busy)
            self.assertFalse(self.app.download_page.busy)
            self.assertFalse(self.app.compare_page.busy)
            self.root.after(1, lambda: heartbeats.append(True))
            self.app.notebook.select(self.app.download_page)
            self.wait_until(lambda: bool(heartbeats))
            self.worker_release.set()
            self.wait_until(lambda: not page.busy)
        self.assertNotEqual(worker_threads, [get_ident()])
        self.assertEqual(completion_threads, [get_ident()])

    def test_analysis_file_and_folder_route_independently(self):
        page = self.app.analysis_page
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / 'judgment.txt'
            source.write_text('判決全文', encoding='utf-8')
            output = folder / 'results'
            page.output.set(str(output))
            for selected in (source, folder):
                page.source.set(str(selected))
                report = {'total': 1, 'files': [str(source)], 'outputs': ['result.analysis.json'], 'errors': []}
                with patch('judgment_app.gui.analysis_page.analyze_source', return_value=report) as analyze:
                    page.start_analysis()
                    self.wait_until(lambda: not page.busy)
                    self.assertEqual(analyze.call_args.args, (selected, output))
            self.assertIn('成功 1 份', page.status.get())
            self.assertFalse(self.app.download_page.cases)

    def test_real_comparison_file_and_folder_and_empty_result_refresh(self):
        page = self.app.compare_page
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            excel = folder / 'cases.xlsx'
            source = folder / 'analysis.json'
            source.write_text(json.dumps({'case_number': '臺灣臺北地方法院114年度訴字第1號', 'defendants': []}), encoding='utf-8')
            columns = ['裁判機關', '裁判年度', '裁判冠字', '裁判號數', '姓名']
            pd.DataFrame([['TPD', 114, '訴', 1, '王小明']], columns=columns).to_excel(excel, index=False)
            destination = folder / 'reports' / 'comparison.json'
            page.output.set(str(destination))
            for excel_input, json_input in ((excel, source), (folder, folder)):
                page.comparison_file.set(str(excel_input))
                page.json_source.set(str(json_input))
                page.start_comparison()
                self.wait_until(lambda: not page.busy)
                self.assertTrue(destination.is_file())
                report = json.loads(destination.read_text())
                self.assertEqual(report['summary']['total'], 1)
                self.assertEqual(len(page.comparison_tree.get_children()), 1)
                self.assertEqual(page.comparison_tree.item(page.comparison_tree.get_children()[0], 'values')[5], 'unmatched')
            page.task_completed('comparison', ({'summary': {'total': 0}, 'rows': [], 'errors': []}, destination))
            self.assertFalse(page.comparison_tree.get_children())

    def test_large_table_yields_to_tk_and_restores_controls(self):
        page = self.app.compare_page
        page.render_comparison([{'row': i, 'status': 'unmatched', 'reason': '沒有判決'} for i in range(601)], [])
        self.assertTrue(page.busy)
        self.assertEqual(len(page.comparison_tree.get_children()), 200)
        self.wait_until(lambda: not page.busy)
        self.assertEqual(len(page.comparison_tree.get_children()), 601)

    def test_worker_error_is_displayed_and_controls_recover(self):
        page = self.app.analysis_page
        with patch('judgment_app.gui.common.messagebox.showerror') as error:
            page.run_task('analysis', lambda: (_ for _ in ()).throw(OSError('測試寫入失敗')))
            self.wait_until(lambda: not page.busy)
            error.assert_called_once()
        self.assertIn('OSError: 測試寫入失敗', page.log.get('1.0', 'end'))

    def test_download_excel_selection_detects_layout_then_reads_cases(self):
        page = self.app.download_page
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'cases.xlsx'
            pd.DataFrame([['法院', '年度', '字別', '號數'],
                          ['TPD', 114, '訴', 123]]).to_excel(source, header=False, index=False)
            with patch('judgment_app.gui.download_page.filedialog.askopenfilename', return_value=str(source)):
                page.choose_file()
            self.assertTrue(page.busy)
            self.wait_until(lambda: not page.busy)
            self.assertEqual([column.get() for column in page.columns], ['A', 'B', 'C', 'D'])
            self.assertEqual(page.start_row.get(), '2')
            self.assertEqual(str(page.read_button['state']), 'normal')
            page.read_cases()
            self.wait_until(lambda: not page.busy)
            self.assertEqual(len(page.visible_cases()), 1)
            self.assertEqual(page.visible_cases()[0].number, 123)

    def test_download_excel_layout_failure_unlocks_page(self):
        page = self.app.download_page
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'invalid.xlsx'
            pd.DataFrame([['沒有案號']]).to_excel(source, header=False, index=False)
            with patch('judgment_app.gui.download_page.filedialog.askopenfilename', return_value=str(source)), patch('judgment_app.gui.download_page.messagebox.showerror') as error:
                page.choose_file()
                self.wait_until(lambda: not page.busy)
                error.assert_called_once()
            self.assertEqual(str(page.sheet_box['state']), 'readonly')
            self.assertIn('找不到', page.log.get('1.0', 'end'))

    def test_comparison_scrollbar_after_dragging_column_width(self):
        page = self.app.compare_page
        self.app.notebook.select(page)
        self.root.geometry('1000x800')
        self.root.deiconify()
        self.root.update()
        tree = page.comparison_tree
        tree.insert('', 'end', values=('source.xlsx', 'Sheet1', 2, '案號', '姓名', 'different', '差異', 'result.json'))
        self.root.update()
        width = tree.column('excel', 'width')
        # 找到表頭分隔線，以真正的滑鼠事件觸發 Treeview 的欄寬調整。
        separator = next((x, y) for y in range(1, 40) for x in range(width - 5, width + 6)
                         if tree.identify_region(x, y) == 'separator')
        x, y = separator
        tree.event_generate('<ButtonPress-1>', x=x, y=y)
        tree.event_generate('<B1-Motion>', x=x + 100, y=y)
        tree.event_generate('<ButtonRelease-1>', x=x + 100, y=y)
        self.root.update()
        self.assertGreater(tree.column('excel', 'width'), width)
        scroll = next(widget for widget in page.comparison_frame.winfo_children()
                      if widget.winfo_class() == 'TScrollbar' and str(widget['orient']) == 'horizontal')
        self.assertLess(scroll.get()[1] - scroll.get()[0], 1)
        command = scroll['command']
        self.root.tk.call(command, 'moveto', 1.0)
        self.root.update()
        self.assertGreater(tree.xview()[0], 0)
        self.assertAlmostEqual(tree.xview()[1], 1)
        self.root.tk.call(command, 'moveto', 0.0)
        self.root.update()
        self.assertEqual(tree.xview()[0], 0)
