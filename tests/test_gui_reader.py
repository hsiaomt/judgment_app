"""用暫存 Excel 驗證介面使用的讀取入口；不啟動視窗。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from judgment_app.case_number import CaseNumber
from judgment_app.gui import read_case_numbers
from judgment_app.acquisition.read_case_numbers import detect_case_number_layout


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
            with self.assertRaisesRegex(ValueError, "第 2 列號數"):
                read_case_numbers(path, "A", "B", "C", "D")


if __name__ == "__main__":
    unittest.main()
