from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from judgment_app.comparison.compare_common import (
    excel_bool, excel_integer, excel_optional_text, find_excel_files,
    iter_excel_rows, read_excel_header,
)
from judgment_app.comparison.compare_law_penalty import _excel_col_to_val
from judgment_app.comparison.compare_judgment import _row_values
from judgment_app.case_number import CaseNumber


class CompareCommonTests(unittest.TestCase):
    def test_excel_file_selection_excludes_temporary_and_generated_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / 'nested'
            nested.mkdir()
            for name in ('a.XLSX', 'b.xls', '~$locked.xlsx', 'a.status.xlsx',
                         'a.status-2.XLSX', 'unrelated.json'):
                (root / name).touch()
            (nested / 'c.xlsx').touch()
            self.assertEqual(find_excel_files(root),
                             [root / 'a.XLSX', root / 'b.xls', nested / 'c.xlsx'])
            # 明確指定結果檔時允許讀取；資料夾搜尋才排除。
            self.assertEqual(find_excel_files(root / 'a.status.xlsx'), [root / 'a.status.xlsx'])
            with self.assertRaisesRegex(ValueError, '找不到 Excel'):
                find_excel_files(root / 'unrelated.json')
            with self.assertRaisesRegex(ValueError, '路徑不存在'):
                find_excel_files(root / 'missing')

    def test_headers_and_rows_preserve_first_column_and_original_positions(self):
        table = pd.DataFrame([
            ['標題'],
            ['裁判機關', '裁判年度', '裁判冠字', '裁判號數', '姓名', '拘役', '拘役', '', 'status', 'reason'],
            ['TPD', 114, '訴', 1, '甲', 30, 90, '無標題內容', 'old', 'old'],
            [None, float('nan'), '', ' ', None, None, 90, '忽略', 'old', 'old'],
            ['TPD', 114, '訴', 2, '乙', 40, 100],
        ], dtype=object)
        original = table.copy(deep=True)
        header, columns = read_excel_header(table)
        self.assertEqual(header, 1)
        self.assertEqual(columns['拘役'], 5)
        self.assertFalse({'', 'status', 'reason'} & columns.keys())
        rows = list(iter_excel_rows(table, header, columns, Path('a.xlsx'), '明細'))
        self.assertEqual([row['拘役'] for row, _ in rows], [30, 40])
        self.assertEqual([location for _, location in rows], [
            {'excel': 'a.xlsx', 'sheet': '明細', 'row': 3},
            {'excel': 'a.xlsx', 'sheet': '明細', 'row': 5},
        ])
        pd.testing.assert_frame_equal(table, original)

    def test_missing_identity_header_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '找不到裁判案號及姓名標題列'):
            read_excel_header(pd.DataFrame([['姓名', '序號'], ['甲', 1]]))

    def test_boolean_spellings_and_nullable_blank(self):
        for value in (True, 1, 1.0, '是', 'Y', 'true', '✓'):
            with self.subTest(value=value):
                self.assertIs(excel_bool(value, column='未遂'), True)
        for value in (False, 0, 0.0, '否', 'N', 'false', None, '', ' ', float('nan'), pd.NA):
            with self.subTest(value=value):
                self.assertIs(excel_bool(value, column='未遂'), False)
        self.assertIsNone(excel_bool(None, column='檢察官上訴', blank=None))
        self.assertIs(excel_bool(0, column='檢察官上訴', blank=None), False)
        for value in ('x', 2, '不明'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '未遂布林值無法辨識'):
                excel_bool(value, column='未遂')

    def test_integer_null_markers_and_zero_policy(self):
        for value in (None, '', ' ', float('nan'), pd.NA, 'x', ' X '):
            with self.subTest(value=value):
                self.assertIsNone(excel_integer(value))
                self.assertIsNone(excel_optional_text(value))
        for value in (0, '0', 0.0):
            self.assertEqual(excel_integer(value), 0)
            self.assertIsNone(excel_integer(value, zero_is_none=True))
        self.assertEqual(excel_integer('12.0'), 12)
        for value in (-1, 1.5, '不明', float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                excel_integer(value)

    def test_penalty_formats_stay_specific_to_each_comparison(self):
        case = CaseNumber.from_string('臺灣高等法院115年度上訴字第1號')
        self.assertEqual(_excel_col_to_val('徒刑(YYMM)', 102), 14)
        self.assertEqual(_row_values({'個案徒刑': 10200, '應執徒刑': 10500}, case)['penalty'], 14)
        self.assertEqual(_row_values({'個案徒刑': 10200, '應執徒刑': 10500}, case)['executed_penalty'], 17)
        with self.assertRaisesRegex(ValueError, 'YYMM 月份不合法'):
            _excel_col_to_val('徒刑(YYMM)', 112)
        for value in (10201, 11200):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '徒刑年月日無法轉為整月'):
                _row_values({'個案徒刑': value}, case)
        self.assertIsNone(_excel_col_to_val('徒刑(YYMM)', 0))
        self.assertIsNone(_row_values({'個案徒刑': 0}, case)['penalty'])
        self.assertEqual(_excel_col_to_val('條段', 'b'), 'B')
        self.assertIsNone(_excel_col_to_val('罰金', 0))

    def test_execution_fields_preserve_null_zero_and_alias_rules(self):
        case = CaseNumber.from_string('臺灣高等法院115年度上訴字第1號')
        values = _row_values({'檢察官上訴': None, '次數': 0, '個案罰金': 0,
                              '個案徒刑易科標準': '易科一仟折算'}, case)
        self.assertIsNone(values['is_pros_appeal'])
        self.assertEqual(values['times'], 0)
        self.assertIsNone(values['fine'])
        self.assertEqual(values['penalty_to_fine'], 1000)
        with self.assertRaisesRegex(ValueError, '檢察官上訴布林值無法辨識'):
            _row_values({'檢察官上訴': 'x'}, case)
        with self.assertRaisesRegex(ValueError, '同一欄位的標題別名內容不一致'):
            _row_values({'累犯': 1, 'is_recidivist': False}, case)
