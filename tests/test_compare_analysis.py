from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from judgment_app.analysis.compare_analysis import compare_files


class CompareAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / '判決.analysis.json'
        self.excel = self.root / '檢核.xlsx'
        self.row = {'裁判機關': '臺灣高院', '裁判年度': 115, '裁判冠字': '上訴',
                    '裁判號數': '001299', '姓名': '甲', '裁判日期': 1150610,
                    '案由': '刑法', '條': 339, '之': None, '項': 1, '項段': 'x', '款': None,
                    '徒刑(YYMM)': 402, '拘役': 0, '罰金': 0, '併科罰金': 0,
                    '未遂': None, '幫助': None, '累犯': None, '裁判情形': '科刑'}
        self.data = {'jid': 'TPHM,115,上訴,1299,20260610,1', 'defendants': [
            {'name': '甲', 'is_recidivist': False, 'crimes': [
                {'crime_id': 1, 'penalty': 50, 'detention': None, 'fine': None,
                 'addition_fine': None, 'is_attempted': False, 'is_accessory': False,
                 'laws': [{'law_name': '刑法', 'article': 339, 'article_sub': None,
                           'paragraph': 1, 'paragraph_part': None, 'paragraph_sub': None}]}],
             'execution_groups': [{'crime_ids': [1], 'is_guilty': True, 'executed_penalty': 60}]}]}

    def run_report(self):
        self.source.write_text(json.dumps(self.data), encoding='utf-8')
        with pd.ExcelWriter(self.excel) as writer:
            pd.DataFrame([self.row]).to_excel(writer, index=False, startrow=2)
        return compare_files(self.excel, self.root)['rows'][0]

    def test_equal_after_unit_and_null_normalization(self):
        result = self.run_report()
        self.assertEqual(result['status'], 'equal')
        self.assertEqual(result['row'], 4)

    def test_difference_preserves_values(self):
        self.row['累犯'] = '是'
        result = self.run_report()
        self.assertEqual(result['status'], 'different')
        diff = result['differences'][0]
        self.assertEqual(diff['excel_raw'], '是')
        self.assertEqual((diff['excel_value'], diff['json_value']), (True, False))

    def test_excel_law_name_normalization(self):
        for raw, expected in (
            ('詐欺', '刑法'),
            ('傷害罪', '刑法'),
            ('違反個人資料保護', '刑法'),
            ('個人資料保護', '個人資料保護法'),
            (' 個人資料保護\n', '個人資料保護法'),
            ('詐欺犯罪防制條例', '詐欺犯罪危害防制條例'),
            ('毒品防制條例', '毒品危害防制條例'),
            ('刑法', '刑法'),
            ('洗錢防制法', '洗錢防制法'),
            ('詐欺犯罪危害防制條例', '詐欺犯罪危害防制條例'),
        ):
            with self.subTest(raw=raw):
                self.row['案由'] = raw
                self.data['defendants'][0]['crimes'][0]['laws'][0]['law_name'] = expected
                self.assertEqual(self.run_report()['status'], 'equal')

    def test_normalized_law_name_selects_crime(self):
        crimes = self.data['defendants'][0]['crimes']
        other = deepcopy(crimes[0])
        other['crime_id'] = 2
        other['laws'][0]['law_name'] = '個人資料保護法'
        crimes.append(other)
        self.row['案由'] = '詐欺'
        result = self.run_report()
        self.assertEqual(result['status'], 'equal')
        self.assertIn(1, result['candidate_crime_ids'])

    def test_single_candidate_with_different_law_is_unmatched(self):
        self.row['案由'] = '個人資料保護'
        result = self.run_report()
        self.assertEqual(result['status'], 'unmatched')
        self.assertEqual(result['candidate_crime_ids'], [])

    def test_law_object_compares_every_field(self):
        law = self.data['defendants'][0]['crimes'][0]['laws'][0]
        for field, value in (('law_name', '洗錢防制法'), ('article', 340),
                             ('article_sub', 1), ('paragraph', 2),
                             ('paragraph_part', 'B'), ('paragraph_sub', 1)):
            with self.subTest(field=field):
                original = law[field]
                law[field] = value
                self.assertEqual(self.run_report()['status'], 'unmatched')
                law[field] = original

    def test_missing_excel_law_name_is_invalid(self):
        for value in (None, 'x'):
            with self.subTest(value=value):
                self.row['案由'] = value
                self.assertEqual(self.run_report()['status'], 'invalid')

    def test_omitted_optional_law_fields_default_to_none(self):
        del self.row['之']
        del self.data['defendants'][0]['crimes'][0]['laws'][0]['article_sub']
        self.assertEqual(self.run_report()['status'], 'equal')

    def test_multiple_judgments_are_not_arbitrarily_selected(self):
        self.run_report()
        (self.root / '另一份.analysis.json').write_text(json.dumps(self.data))
        self.assertEqual(self.run_report()['status'], 'equal')

    def test_multiple_documents_keep_all_differences_with_sources(self):
        self.run_report()
        other = deepcopy(self.data)
        other['defendants'][0]['crimes'][0]['is_attempted'] = True
        second = self.root / '另一份.analysis.json'
        second.write_text(json.dumps(other))
        result = self.run_report()
        self.assertEqual(result['status'], 'different')
        self.assertEqual(len(result['matched_candidates']), 2)
        self.assertEqual(result['differences'][0]['json'], str(second))
        self.assertEqual(len(result['document_results']), 2)

    def test_search_continues_past_unmatched_or_invalid_document(self):
        for other in ({**self.data, 'defendants': []}, {**self.data, 'defendants': [{}]}):
            with self.subTest(other=other):
                (self.root / '另一份.analysis.json').write_text(json.dumps(other))
                result = self.run_report()
                self.assertEqual(result['status'], 'equal')
                self.assertEqual(result['matched_candidates'], [{'json': str(self.source), 'crime_id': 1}])

    def test_multiple_documents_without_match(self):
        self.row['姓名'] = '不存在'
        (self.root / '另一份.analysis.json').write_text(json.dumps(self.data))
        result = self.run_report()
        self.assertEqual(result['status'], 'unmatched')
        self.assertEqual(len(result['document_results']), 2)

    def test_masked_name_matches_unique_prefix(self):
        for excel_name, real_name in (
            ('甲○○', '甲乙丙'),
            ('陳小○', '陳小明'),
            ('Amy○○', 'AmyChen'),
            ('陳A○○', '陳Amy'),
            (' 陳 小 ○○ ', '陳小明'),
        ):
            with self.subTest(excel_name=excel_name):
                self.row['姓名'] = excel_name
                self.data['defendants'][0]['name'] = real_name
                self.assertEqual(self.run_report()['status'], 'equal')

    def test_masked_name_with_multiple_matches_is_unmatched(self):
        self.row['姓名'] = '陳○○'
        defendant = self.data['defendants'][0]
        defendant['name'] = '陳小明'
        other = deepcopy(defendant)
        other['name'] = '陳大華'
        self.data['defendants'].append(other)
        self.assertEqual(self.run_report()['status'], 'unmatched')

    def test_masked_name_without_usable_match_is_unmatched(self):
        for name in ('乙○○', '○○', '', '123○○'):
            with self.subTest(name=name):
                self.row['姓名'] = name
                self.assertEqual(self.run_report()['status'], 'unmatched')

    def test_unmasked_name_requires_full_match(self):
        self.data['defendants'][0]['name'] = '甲乙丙'
        self.assertEqual(self.run_report()['status'], 'unmatched')

    def test_duplicate_crimes_are_matched(self):
        self.data['defendants'][0]['crimes'] *= 2
        result = self.run_report()
        self.assertEqual(result['status'], 'equal')
        self.assertEqual(result['candidate_crime_ids'], [1])

    def test_multiple_matching_crimes_preserve_candidates(self):
        crimes = self.data['defendants'][0]['crimes']
        other = deepcopy(crimes[0])
        other['crime_id'] = 2
        crimes.append(other)
        self.data['defendants'][0]['execution_groups'][0]['crime_ids'].append(2)
        result = self.run_report()
        self.assertEqual(result['status'], 'equal')
        self.assertIn(1, result['candidate_crime_ids'])
        self.assertEqual(result['candidate_crime_ids'], [1, 2])

    def test_candidate_selection_uses_penalties(self):
        crimes = self.data['defendants'][0]['crimes']
        for field, value in (('penalty', 51), ('detention', 10), ('fine', 1000)):
            with self.subTest(field=field):
                other = deepcopy(crimes[0])
                other.update(crime_id=2)
                other[field] = value
                crimes[:] = [crimes[0], other]
                result = self.run_report()
                self.assertEqual(result['status'], 'equal')
                self.assertIn(1, result['candidate_crime_ids'])

    def test_candidate_selection_without_matching_penalty_is_unmatched(self):
        self.data['defendants'][0]['crimes'] *= 2
        self.row['徒刑(YYMM)'] = 403
        self.assertEqual(self.run_report()['status'], 'unmatched')

    def test_execution_group_penalties_are_not_compared(self):
        defendant = self.data['defendants'][0]
        other = deepcopy(defendant['crimes'][0])
        other['crime_id'] = 2
        other['penalty'] = 60
        defendant['crimes'].append(other)
        defendant['execution_groups'] = [
            {'crime_ids': [1], 'is_guilty': True, 'executed_penalty': 60,
             'executed_detention': None, 'executed_fine': None, 'executed_addition_fine': None},
            {'crime_ids': [2], 'is_guilty': True, 'executed_penalty': 50,
             'executed_detention': None, 'executed_fine': None, 'executed_addition_fine': None},
        ]
        result = self.run_report()
        self.assertEqual(result['status'], 'equal')
        self.assertIn(1, result['candidate_crime_ids'])
        self.assertEqual(result['differences'], [])

    def test_excel_guilty_status_is_not_compared(self):
        for value in ('無罪', '科刑', None, '其他'):
            with self.subTest(value=value):
                self.row['裁判情形'] = value
                self.assertEqual(self.run_report()['status'], 'equal')
        report = compare_files(self.excel, self.root)
        self.assertIn('裁判情形', report['sheets'][0]['uncompared_columns'])

    def test_multiple_guilty_groups_do_not_require_unique_group(self):
        groups = self.data['defendants'][0]['execution_groups']
        groups.append(deepcopy(groups[0]))
        result = self.run_report()
        self.assertEqual(result['status'], 'equal')
        self.assertEqual(result['uncomparable'], [])

    def test_no_guilty_group_is_unmatched(self):
        defendant = self.data['defendants'][0]
        for groups in ([], [{'crime_ids': [1], 'is_guilty': False}],
                       [{'crime_ids': [1]}]):
            with self.subTest(groups=groups):
                defendant['execution_groups'] = groups
                result = self.run_report()
                self.assertEqual(result['status'], 'unmatched')
                self.assertEqual(result['reason'], '找不到有罪的 group')

    def test_only_crimes_in_guilty_groups_can_match(self):
        defendant = self.data['defendants'][0]
        other = deepcopy(defendant['crimes'][0])
        other['crime_id'] = 2
        defendant['crimes'].append(other)
        defendant['execution_groups'] = [
            {'crime_ids': [1], 'is_guilty': False},
            {'crime_ids': [2], 'is_guilty': True},
        ]
        result = self.run_report()
        self.assertEqual(result['status'], 'equal')
        self.assertEqual(result['candidate_crime_ids'], [2])
        self.assertEqual(result['candidate_crime_ids'], [2])
        other['penalty'] = 60
        self.assertEqual(self.run_report()['status'], 'unmatched')

    def test_crime_outside_groups_is_not_a_candidate(self):
        self.data['defendants'][0]['execution_groups'][0]['crime_ids'] = [2]
        self.assertEqual(self.run_report()['status'], 'unmatched')

    def test_missing_json_field_is_incomplete(self):
        del self.data['defendants'][0]['is_recidivist']
        self.assertEqual(self.run_report()['status'], 'incomplete')

    def test_absent_excel_recidivist_column_is_skipped(self):
        del self.row['累犯']
        del self.data['defendants'][0]['is_recidivist']
        self.assertEqual(self.run_report()['status'], 'equal')

    def test_addition_fine_is_not_compared(self):
        self.row['併科罰金'] = '不比較'
        self.assertEqual(self.run_report()['status'], 'equal')
        report = compare_files(self.excel, self.root)
        self.assertIn('併科罰金', report['sheets'][0]['uncompared_columns'])

    def test_single_candidate_must_match_all_location_fields(self):
        crime = self.data['defendants'][0]['crimes'][0]
        for field, value in (('penalty', 51), ('detention', 10), ('fine', 1000)):
            with self.subTest(field=field):
                original = crime[field]
                crime[field] = value
                self.assertEqual(self.run_report()['status'], 'unmatched')
                crime[field] = original

    def test_missing_candidate_field_is_unmatched(self):
        del self.data['defendants'][0]['crimes'][0]['penalty']
        self.assertEqual(self.run_report()['status'], 'unmatched')

    def test_recidivist_is_compared_after_candidate_selection(self):
        self.row['累犯'] = '是'
        result = self.run_report()
        self.assertEqual(result['status'], 'different')
        self.assertEqual(result['differences'][0]['field'], 'is_recidivist')

    def test_attempt_and_accessory_are_differences_not_location_conditions(self):
        crime = self.data['defendants'][0]['crimes'][0]
        crime['is_attempted'] = True
        crime['is_accessory'] = True
        other = deepcopy(crime)
        other.update(crime_id=2, is_attempted=False, is_accessory=False)
        self.data['defendants'][0]['crimes'].append(other)
        self.data['defendants'][0]['execution_groups'][0]['crime_ids'].append(2)
        result = self.run_report()
        self.assertEqual(result['status'], 'different')
        self.assertEqual(result['candidate_crime_ids'], [1, 2])
        self.assertCountEqual([d['column'] for d in result['differences']], ['幫助', '未遂'])

    def test_all_candidates_report_differences_and_missing_fields(self):
        defendant = self.data['defendants'][0]
        first = defendant['crimes'][0]
        second = deepcopy(first)
        second.update(crime_id=2, is_attempted=True, is_accessory=True)
        third = deepcopy(second)
        third['crime_id'] = 3
        del third['is_accessory']
        third['laws'] *= 2
        defendant['crimes'].extend([second, third])
        defendant['execution_groups'][0]['crime_ids'] = [1, 2, 3]
        self.row['累犯'] = '是'
        result = self.run_report()
        self.assertEqual(result['status'], 'different')
        self.assertEqual(result['candidate_crime_ids'], [1, 2, 3])
        self.assertCountEqual([d['field'] for d in result['differences']], [
            'crimes[crime_id=2].is_attempted', 'crimes[crime_id=2].is_accessory',
            'crimes[crime_id=3].is_attempted', 'is_recidivist'])
        self.assertEqual([d['field'] for d in result['uncomparable']],
                         ['crimes[crime_id=3].is_accessory'])

    def test_missing_attempt_and_accessory_are_incomplete(self):
        crime = self.data['defendants'][0]['crimes'][0]
        del crime['is_attempted']
        del crime['is_accessory']
        result = self.run_report()
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(len(result['uncomparable']), 2)

    def test_invalid_month_is_reported(self):
        self.row['徒刑(YYMM)'] = 499
        self.assertEqual(self.run_report()['status'], 'invalid')

    def test_date_does_not_disambiguate_same_case(self):
        self.run_report()
        other = dict(self.data, jid='TPHM,115,上訴,1299,20260611,1')
        (self.root / '另一份.analysis.json').write_text(json.dumps(other))
        self.assertEqual(self.run_report()['status'], 'equal')

    def test_date_is_not_used_for_matching(self):
        for date in (1150611, '無效日期', None):
            with self.subTest(date=date):
                self.row['裁判日期'] = date
                self.assertEqual(self.run_report()['status'], 'equal')
        report = compare_files(self.excel, self.root)
        self.assertIn('裁判日期', report['sheets'][0]['uncompared_columns'])
