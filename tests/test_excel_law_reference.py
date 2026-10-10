import unittest

from judgment_app.analysis.models import LawReference
from judgment_app.analysis.compare import excel_row_to_law_reference


class ExcelLawReferenceTests(unittest.TestCase):
    def test_converts_excel_fields_without_changing_row(self):
        row = {'案由': '詐欺', '條': 339.0, '之': '4', '項': 1,
               '項段': 'b', '款': '2'}
        original = row.copy()
        law = excel_row_to_law_reference(row)
        self.assertEqual(law, LawReference('刑法', 339, 4, 1, 'B', 2))
        self.assertEqual(row, original)

    def test_alias_and_optional_null_fields(self):
        law = excel_row_to_law_reference(
            {'案由': '個人資料保護', '條': 41, '之': 'x', '項': float('nan')})
        self.assertEqual(law, LawReference('個人資料保護法', 41))

    def test_missing_required_fields(self):
        for row in ({}, {'案由': '刑法'}, {'條': 339}, {'案由': 'x', '條': 339}):
            with self.subTest(row=row), self.assertRaises(ValueError):
                excel_row_to_law_reference(row)

    def test_invalid_law_fields(self):
        for override in ({'條': 0}, {'條': 1.5}, {'項': -1}, {'項段': 'D'}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                excel_row_to_law_reference({'案由': '刑法', '條': 339, **override})
