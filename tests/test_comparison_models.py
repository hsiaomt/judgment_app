from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from judgment_app.comparison.common import load_comparison_report
from judgment_app.comparison.models import ComparisonReport, ComparisonResult


class ComparisonReportTests(unittest.TestCase):
    def test_results_require_reason_for_non_equal(self):
        self.assertEqual(ComparisonResult(status='equal').status, 'equal')
        self.assertEqual(ComparisonResult(status='unmatched', reason='累犯不合').status, 'unmatched')
        self.assertEqual(ComparisonResult(status='judgment_not_found', reason='判決不存在').status,
                         'judgment_not_found')
        for row in ({'status': 'unmatched'}, {'status': 'unmatched', 'reason': None},
                    {'status': 'unmatched', 'reason': ''}, {'status': 'unmatched', 'reason': ' \n'},
                    {'status': 'unmatched', 'reason': 123}, {'status': 'equal', 'reason': '累犯不合'},
                    {'status': 'judgment_not_found'}, {'status': 'judgment_not_found', 'reason': ''},
                    {'status': 'unknown'}, {'status': 'different'}, {'status': ['equal']}):
            with self.subTest(row=row), self.assertRaises(ValidationError):
                ComparisonReport.model_validate({'rows': [row], 'errors': []})

    def test_missing_judgment_report_can_be_loaded_and_counted(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            path.write_text(json.dumps({'rows': [
                {'status': 'judgment_not_found', 'reason': '判決不存在'},
                {'status': 'unmatched', 'reason': '法條不合'},
            ], 'errors': []}), encoding='utf-8')
            report = load_comparison_report(path)
        self.assertEqual(report['summary'], {
            'judgment_not_found': 1, 'unmatched': 1, 'total': 2, 'errors': 0,
        })

    def test_excel_output_requires_valid_source_and_output(self):
        for outputs in ([None], [{}], [{'source': 'a.xlsx'}],
                        [{'source': 'a.xlsx', 'output': None}],
                        [{'source': 'a.xlsx', 'output': ' '}],
                        [{'source': 123, 'output': 'b.xlsx'}], 'a.xlsx', None):
            with self.subTest(outputs=outputs), self.assertRaises(ValidationError):
                ComparisonReport.model_validate({'rows': [], 'errors': [], 'excel_outputs': outputs})

    def test_nested_results_and_candidates_are_validated(self):
        for extra in ({'document_results': [{'status': 'unmatched'}]},
                      {'document_results': [None]},
                      {'matched_candidates': [{'json': None, 'crime_id': 1}]},
                      {'candidate_crime_ids': ['1']}, {'row': True}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                ComparisonReport.model_validate({'rows': [{'status': 'equal', **extra}], 'errors': []})

    def test_round_trip_preserves_context_and_recomputes_summary(self):
        data = {'rows': [{'status': 'equal', 'json': 'a.json', 'candidate_crime_ids': [1],
                         'matched_candidates': [{'json': 'a.json', 'crime_id': 1}],
                         'document_results': [{'status': 'equal', 'json': 'a.json'},
                                              {'status': 'unmatched', 'reason': '累犯不合'}]}],
                'errors': [{'error': '無法寫入', 'file': 'source.xlsx'}],
                'excel_outputs': [{'source': 'source.xls', 'output': 'source.status.xlsx'}],
                'summary': {'total': 999}, 'custom_version': 1}
        original = deepcopy(data)
        actual = ComparisonReport.model_validate(data).to_dict()
        self.assertEqual(data, original)
        self.assertEqual(actual['rows'], data['rows'])
        self.assertEqual(actual['excel_outputs'], data['excel_outputs'])
        self.assertEqual(actual['custom_version'], 1)
        self.assertEqual(actual['summary'], {'equal': 1, 'total': 1, 'errors': 1})
        self.assertNotIn('reason', actual['rows'][0])
        self.assertNotIn('differences', actual['rows'][0])
        self.assertEqual(ComparisonReport.model_validate(actual).to_dict(), actual)

    def test_old_statuses_and_fields_are_rejected_without_modifying_file(self):
        rows = [{'status': status} for status in ('different', 'incomplete', 'invalid', 'unmatch')]
        rows += [{'status': 'equal', 'uncomparable': []}]
        rows += [{'status': 'equal', 'document_results': [row]} for row in rows.copy()]
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            for row in rows:
                with self.subTest(row=row):
                    path.write_text(json.dumps({'rows': [row], 'errors': []}))
                    original = path.read_bytes()
                    with self.assertRaises(ValueError):
                        load_comparison_report(path)
                    self.assertEqual(path.read_bytes(), original)

    def test_invalid_report_error_contains_source_and_field(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            path.write_text('{"rows": [], "errors": [], "excel_outputs": [null]}')
            with self.assertRaises(ValueError) as error:
                load_comparison_report(path)
            self.assertIn(str(path), str(error.exception))
            self.assertIn('excel_outputs.0', str(error.exception))
            self.assertIsInstance(error.exception.__cause__, ValidationError)

    def test_grouped_report_round_trip_preserves_nested_values_and_counts_results(self):
        locations = [{'excel': 'a.xlsx', 'sheet': '明細', 'row': row} for row in (4, 5)]
        difference = {'field': 'defendants[0].crimes[]',
                      'excel': {'penalty': 14, 'laws': [None, True, '刑法', 1.5]}, 'json': None}
        data = {
            'rows': [{'status': 'unmatched', 'reason': '刑度不合', 'excel_rows': locations,
                      'document_results': [{'status': 'unmatched', 'reason': '刑度不合',
                                            'json': 'a.json', 'differences': [difference]}]},
                     {'status': 'equal', 'differences': []}],
            'errors': [{'error': '資料不一致', 'excel_rows': locations,
                        'case_number': '案號', 'name': '甲', 'stage': 'build_judgment'}],
            'sheets': [{'file': 'a.xlsx', 'sheet': '明細', 'defaulted_fields': ['is_attempted']}],
            'ignored_fields': ['crime_id'], 'summary': {'total': 999},
        }
        original = deepcopy(data)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            path.write_text(json.dumps(data), encoding='utf-8-sig')
            before = path.read_bytes()
            actual = load_comparison_report(path)
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(actual['summary'], {'unmatched': 1, 'equal': 1, 'total': 2, 'errors': 1})
        self.assertEqual({k: v for k, v in actual.items() if k != 'summary'},
                         {k: v for k, v in data.items() if k != 'summary'})
        self.assertEqual(data, original)
        self.assertEqual(ComparisonReport.model_validate(actual).to_dict(), actual)

    def test_grouped_locations_and_nested_differences_are_validated(self):
        location = {'excel': 'a.xlsx', 'sheet': '明細', 'row': 1}
        invalid = [
            {'excel_rows': [{**location, 'row': row}]} for row in (0, True, '1')
        ] + [
            {'excel_rows': [{'excel': 'a.xlsx', 'row': 1}]},
            {'differences': [{'field': 'penalty', 'excel': 1}]},
            {'differences': [{'field': '', 'excel': 1, 'json': 2}]},
            {'differences': [{'field': 'penalty', 'excel': object(), 'json': None}]},
            {'differences': [None]},
        ]
        for extra in invalid:
            for nested in (False, True):
                row = {'status': 'unmatched', 'reason': '不合', **extra}
                if nested:
                    row = {'status': 'equal', 'document_results': [row]}
                with self.subTest(extra=extra, nested=nested), self.assertRaises(ValidationError):
                    ComparisonReport.model_validate({'rows': [row], 'errors': []})
        for extra in (
            {'errors': [{'error': '錯誤', 'excel_rows': [{**location, 'row': 0}]}]},
            {'ignored_fields': [123]},
            {'sheets': [{'file': 'a.xlsx', 'sheet': '明細', 'defaulted_fields': [123]}]},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                ComparisonReport.model_validate({'rows': [], 'errors': [], **extra})
