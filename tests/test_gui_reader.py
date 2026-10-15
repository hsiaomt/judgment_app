"""用暫存 Excel 驗證介面使用的讀取入口；不啟動視窗。"""

from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from judgment_app.case_number import CaseNumber
from judgment_app.gui import read_case_numbers
from judgment_app.acquisition.read_case_numbers import detect_case_number_layout
from judgment_app.gui.download_page import DownloadPage


class ReaderIntegrationTests(unittest.TestCase):
    def test_auto_detect_columns_and_start_row(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "auto.xlsx"
            rows = [["報表"], ["法院", "年度", "字別", "號數"],
                    [None] * 26 + ["台北地院", 114, "訴", 219],
                    [None] * 26 + ["TPD", 114, "訴", 219]]
            pd.DataFrame(rows).to_excel(path, header=False, index=False)
            self.assertEqual(detect_case_number_layout(path), (("AA", "AB", "AC", "AD"), 3))
            self.assertEqual(read_case_numbers(path), [CaseNumber("TPD", 114, "訴", 219)])

    def test_detection_errors(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "missing.xlsx"
            pd.DataFrame([["法院", "年度", "字別", "號數"]]).to_excel(path, header=False, index=False)
            with self.assertRaisesRegex(ValueError, "找不到"):
                read_case_numbers(path)
            pd.DataFrame([["台北地院", 114]]).to_excel(path, header=False, index=False)
            with self.assertRaisesRegex(ValueError, "不足三欄"):
                read_case_numbers(path)
            with self.assertRaisesRegex(ValueError, "同時指定"):
                read_case_numbers(path, "A")

    def test_workbook_selection_mapping_and_deduplication(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cases.xlsx"
            rows = [
                ["號數", "法院", "字別", "年度"],
                [219, "臺灣臺北地方法院", "訴", 114],
                [219, "台北地院", "訴", 114],
                [219, "TPDM", "訴", 114],
            ]
            with pd.ExcelWriter(path) as writer:
                pd.DataFrame([["其他工作表"]]).to_excel(writer, sheet_name="其他", header=False, index=False)
                pd.DataFrame(rows).to_excel(writer, sheet_name="案號", header=False, index=False)
            with pd.ExcelFile(path) as workbook:
                self.assertEqual(workbook.sheet_names, ["其他", "案號"])
            result = read_case_numbers(path, "B", "D", "C", "A", sheet_name="案號", start_row=2)
            self.assertEqual(result, [CaseNumber("TPD", 114, "訴", 219)])
            with self.assertRaisesRegex(ValueError, "不同欄位"):
                read_case_numbers(path, "A", "A", "C", "D")
            with self.assertRaisesRegex(ValueError, "超出"):
                read_case_numbers(path, "AL", "AM", "AN", "AO", sheet_name="案號")

    def test_invalid_row_reports_excel_row_number(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.xlsx"
            pd.DataFrame([
                ["法院", "年度", "字別", "號數"],
                ["臺北地院", 114, "訴", "不是數字"],
            ]).to_excel(path, header=False, index=False)
            with self.assertLogs('judgment_app.acquisition.read_case_numbers', level='WARNING') as logs:
                self.assertEqual(read_case_numbers(path, "A", "B", "C", "D"), [])
            self.assertIn("第 2 列號數", logs.output[0])

    def test_invalid_rows_are_skipped_and_valid_rows_are_deduplicated(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "mixed.xlsx"
            pd.DataFrame([
                ["法院", "年度", "字別", "號數"],
                ["TPD", 114, "訴", 1],
                ["不存在的法院", 114, "訴", 2],
                ["TPD", None, "訴", 3],
                ["TPD", "錯誤年度", "訴", 4],
                ["TPD", 114, "訴", "錯誤號數"],
                [None, None, None, None],
                ["TPD", 114, "訴", 1],
                ["TPD", 114, "訴", 5],
            ]).to_excel(path, header=False, index=False)
            for kwargs in ({}, dict(court_column='A', year_column='B', case_column='C',
                                   number_column='D', start_row=2)):
                with self.subTest(kwargs=kwargs):
                    errors = []
                    cases = read_case_numbers(path, on_error=errors.append, **kwargs)
                    self.assertEqual(cases, [CaseNumber("TPD", 114, "訴", n) for n in (1, 5)])
                    self.assertEqual(len(errors), 4)
                    for row, message in zip(range(3, 7), errors):
                        self.assertIn(f"第 {row} 列", message)
                        self.assertIn("已跳過", message)

    def test_gui_import_logs_bad_row_and_merges_valid_cases(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "gui.xlsx"
            pd.DataFrame([
                ["TPD", 114, "訴", 1],
                ["不存在的法院", 114, "訴", 2],
                ["TPD", 114, "訴", 3],
            ]).to_excel(path, header=False, index=False)
            page = DownloadPage.__new__(DownloadPage)
            page.root = Mock()
            page.start_row = Mock(get=Mock(return_value='1'))
            page.file = Mock(get=Mock(return_value=str(path)))
            page.sheet = Mock(get=Mock(return_value=0))
            page.columns = [Mock(get=Mock(return_value=c)) for c in 'ABCD']
            page.status = Mock()
            page.events = Queue()
            page.append_log = Mock()
            page.merge_cases = Mock()
            page.schedule_poll = Mock()
            page.set_busy = Mock()
            page.run_task = lambda kind, operation: page.events.put((kind, operation(), None))
            with patch('judgment_app.gui.download_page.messagebox.showerror') as error:
                page.read_cases()
                page.poll()  # 錯誤列以日誌事件回報。
                page.poll()  # 成功案號照常合併。
                error.assert_not_called()
            self.assertIn('第 2 列', page.append_log.call_args.args[0])
            page.merge_cases.assert_called_once_with([CaseNumber('TPD', 114, '訴', n) for n in (1, 3)])


if __name__ == "__main__":
    unittest.main()
