from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from judgment_app.analysis.compare import load_comparison_report
from judgment_app.analysis.comparison_models import ComparisonReport, ComparisonResult


class ComparisonReportTests(unittest.TestCase):
    def test_results_require_reason_only_for_unmatched(self):
        self.assertEqual(ComparisonResult(status='equal').status, 'equal')
        self.assertEqual(ComparisonResult(status='unmatched', reason='累犯不合').status, 'unmatched')
        for row in ({'status': 'unmatched'}, {'status': 'unmatched', 'reason': None},
                    {'status': 'unmatched', 'reason': ''}, {'status': 'unmatched', 'reason': ' \n'},
                    {'status': 'unmatched', 'reason': 123}, {'status': 'equal', 'reason': '累犯不合'},
                    {'status': 'unknown'}, {'status': 'different'}, {'status': ['equal']}):
            with self.subTest(row=row), self.assertRaises(ValidationError):
                ComparisonReport.model_validate({'rows': [row], 'errors': []})

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
        rows += [{'status': 'equal', field: []} for field in ('differences', 'uncomparable')]
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
