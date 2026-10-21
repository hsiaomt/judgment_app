from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from judgment_app.analysis.analysis_models import JudgmentAnalysis
from judgment_app.analysis.compare_judgment import (
    build_judgment_analysis, compare_analyses, compare_files, compare_to_report,
    excel_row_to_law_reference,
)


class CompareJudgmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.excel = self.root / "執行明細.xlsx"
        self.source = self.root / "判決.analysis.json"
        self.case = "臺灣高等法院115年度上訴字第1299號"
        self.row = {
            "序號": 22598267, "個案序號": 1,
            "裁判機關": "臺灣高院", "裁判年度": 115, "裁判冠字": "上訴",
            "裁判號數": "001299", "姓名": "陳○○", "累犯": None, "上訴類別": "01",
            "應執緩刑期間": 300, "緩刑法條": "01", "應執裁判情形": "科刑",
            "應執徒刑": 10500, "應執徒刑易科標準": "易科一仟折算",
            "應執拘役": 0, "應執拘役易科標準": None, "應執罰金": 0, "應執罰金易勞標準": None,
            "個案裁判情形": "科刑", "次數": 2, "個案案由": "詐欺", "法條": "3394x1x02",
            "個案徒刑": 10200, "個案徒刑易科標準": "易科一仟折算",
            "個案拘役": 0, "個案拘役易科標準": None, "個案罰金": 0, "個案罰金易勞標準": None,
        }
        self.data = {
            "case_number": self.case,
            "defendants": [{
                "name": "陳小明", "is_recidivist": False, "is_pros_appeal": True,
                "probation": 3, "probation_law": 1,
                "crimes": [{"crime_id": 9, "crime_name": "詐欺", "crime_jsst": "科刑",
                            "times": 2, "penalty": 14, "penalty_to_fine": 1000,
                            "laws": [{"law_name": "刑法", "article": 339, "article_sub": 4,
                                      "paragraph": 1, "paragraph_sub": 2,
                                      "is_attempted": False, "is_accessory": False}]}],
                "execution_groups": [{"crime_ids": [9], "executed_jsst": "科刑",
                                      "executed_penalty": 17, "executed_penalty_to_fine": 1000}],
            }],
        }

    def run_report(self, rows=None):
        pd.DataFrame(rows or [self.row]).to_excel(self.excel, index=False, startrow=2)
        self.source.write_text(json.dumps(self.data), encoding="utf-8")
        original = self.excel.read_bytes()
        report = compare_files(self.excel, self.root)
        self.assertEqual(self.excel.read_bytes(), original)
        return report

    def test_full_comparison_and_masked_name(self):
        report = self.run_report()
        self.assertEqual(report["summary"], {"equal": 1, "total": 1, "errors": 0})
        self.assertEqual(report["rows"][0]["excel_rows"][0]["row"], 4)
        self.assertEqual(report["sheets"][0]["defaulted_fields"], ["is_accessory", "is_attempted"])

    def test_composite_key_merges_laws_without_multiplying_times(self):
        second = {**self.row, "法條": "0190x1B00"}
        law = {"law_name": "刑法", "article": 19, "paragraph": 1, "paragraph_part": "B",
               "is_attempted": False, "is_accessory": False}
        self.data["defendants"][0]["crimes"][0]["laws"].append(law)
        analysis = build_judgment_analysis(self.case, [self.row, second, self.row])
        defendant = analysis.defendants[0]
        self.assertEqual(len(defendant.crimes), 1)
        self.assertEqual(defendant.crimes[0].times, 2)
        self.assertEqual(len(defendant.crimes[0].laws), 2)
        self.assertEqual(defendant.execution_groups[0].crime_ids, [1])
        self.assertEqual(self.run_report([second, self.row])["rows"][0]["status"], "equal")

    def test_same_sequence_different_crime_sequences_are_one_group(self):
        defendant = self.data["defendants"][0]
        defendant["crimes"].append({**deepcopy(defendant["crimes"][0]), "crime_id": 10})
        defendant["execution_groups"][0]["crime_ids"].append(10)
        rows = [self.row, {**self.row, "個案序號": 2}]
        analysis = build_judgment_analysis(self.case, rows)
        self.assertEqual(len(analysis.defendants[0].crimes), 2)
        self.assertEqual(analysis.defendants[0].execution_groups[0].crime_ids, [1, 2])
        self.assertEqual(self.run_report(rows)["rows"][0]["status"], "equal")

    def test_different_sequences_same_crime_sequence_are_distinct(self):
        defendant = self.data["defendants"][0]
        defendant["crimes"].append({**deepcopy(defendant["crimes"][0]), "crime_id": 10})
        defendant["execution_groups"].append({**defendant["execution_groups"][0], "crime_ids": [10]})
        rows = [self.row, {**self.row, "序號": 22598268}]
        analysis = build_judgment_analysis(self.case, rows)
        self.assertEqual(len(analysis.defendants[0].crimes), 2)
        self.assertEqual(len(analysis.defendants[0].execution_groups), 2)
        self.assertEqual(self.run_report(rows)["rows"][0]["status"], "equal")

    def test_every_ignored_field(self):
        self.data.update(lower_court_case_number="前審", pros_appearing=["檢察官"])
        defendant = self.data["defendants"][0]
        defendant.update(confiscation=["物品"], appeal_result="駁回")
        defendant["crimes"][0].update(crime_id=42, crime_name="不同罪名", addition_fine=100,
                                       addition_fine_to_detention=200)
        defendant["execution_groups"][0].update(crime_ids=[99], executed_addition_fine=100,
                                                executed_addition_fine_to_detention=200)
        self.row.update(蒞庭檢察官="忽略", 上訴結果="忽略", 個案併科罰金="忽略")
        self.assertEqual(self.run_report()["rows"][0]["status"], "equal")

    def test_nonignored_fields_are_compared(self):
        mutations = [
            ((), "case_number", "臺灣高等法院115年度上訴字第1300號"),
            (("defendants", 0), "is_recidivist", True),
            (("defendants", 0), "is_pros_appeal", False),
            (("defendants", 0), "probation", 2),
            (("defendants", 0), "probation_law", 2),
        ]
        mutations += [(('defendants', 0, 'crimes', 0), field, value) for field, value in (
            ("crime_jsst", "免刑"), ("times", 3), ("penalty", 15), ("penalty_to_fine", 2000),
            ("detention", 10), ("detention_to_fine", 1000), ("fine", 10000), ("fine_to_detention", 1000),
        )]
        mutations += [(('defendants', 0, 'execution_groups', 0), field, value) for field, value in (
            ("executed_jsst", "免刑"), ("executed_penalty", 18), ("executed_penalty_to_fine", 2000),
            ("executed_detention", 10), ("executed_detention_to_fine", 1000),
            ("executed_fine", 10000), ("executed_fine_to_detention", 1000),
        )]
        mutations += [(('defendants', 0, 'crimes', 0, 'laws', 0), field, value) for field, value in (
            ("law_name", "洗錢防制法"), ("article", 340), ("article_sub", 5), ("article_part", "A"),
            ("paragraph", 2), ("paragraph_part", "B"), ("paragraph_sub", 3),
            ("is_attempted", True), ("is_accessory", True),
        )]
        expected = build_judgment_analysis(self.case, [self.row])
        for path, field, value in mutations:
            with self.subTest(field=field):
                data = deepcopy(self.data)
                target = data
                for key in path:
                    target = target[key]
                target[field] = value
                result = compare_analyses(expected, JudgmentAnalysis.from_dict(data))
                self.assertEqual(result["status"], "unmatched")
                self.assertTrue(result["differences"])

    def test_extra_and_missing_items_are_detected(self):
        expected = build_judgment_analysis(self.case, [self.row])
        for path in (("defendants",), ("defendants", 0, "crimes"),
                     ("defendants", 0, "execution_groups"), ("defendants", 0, "crimes", 0, "laws")):
            for extra in (True, False):
                with self.subTest(path=path, extra=extra):
                    data = deepcopy(self.data)
                    target = data
                    for key in path:
                        target = target[key]
                    if extra:
                        item = deepcopy(target[0])
                        if path == ("defendants",):
                            item["name"] = "李大華"
                        target.append(item)
                    else:
                        target.clear()
                    result = compare_analyses(expected, JudgmentAnalysis.from_dict(data))
                    self.assertEqual(result["status"], "unmatched")

    def test_order_does_not_matter_and_inputs_are_unchanged(self):
        data = deepcopy(self.data)
        defendant = data["defendants"][0]
        defendant["crimes"].append({**deepcopy(defendant["crimes"][0]), "crime_id": 10, "times": 3})
        defendant["execution_groups"].append({**defendant["execution_groups"][0], "crime_ids": [10], "executed_penalty": 20})
        defendant["crimes"][0]["laws"].append({**defendant["crimes"][0]["laws"][0], "article": 340})
        data["defendants"].append({**deepcopy(defendant), "name": "李大華"})
        expected = JudgmentAnalysis.from_dict(data)
        for defendant in data["defendants"]:
            for crime in defendant["crimes"]:
                crime["laws"].reverse()
            defendant["crimes"].reverse()
            defendant["execution_groups"].reverse()
        data["defendants"].reverse()
        actual = JudgmentAnalysis.from_dict(data)
        before = actual.model_dump()
        self.assertEqual(compare_analyses(expected, actual)["status"], "equal")
        self.assertEqual(actual.model_dump(), before)

    def test_masked_name_must_be_unique_and_one_to_one(self):
        other = deepcopy(self.data["defendants"][0])
        other.update(name="陳大華", probation=5)
        self.data["defendants"].append(other)
        self.assertIn("被告姓名無法唯一配對", self.run_report()["rows"][0]["reason"])
        self.data["defendants"].pop()
        result = self.run_report([self.row, {**self.row, "姓名": "陳小○"}])["rows"][0]
        self.assertIn("被告姓名無法唯一配對", result["reason"])

    def test_conflicts_fail_whole_case_with_sequence_context(self):
        for change in ({"累犯": 1}, {"個案徒刑": 10300}, {"次數": 3},
                       {"應執徒刑": 10600}, {"應執緩刑期間": 400}, {"上訴類別": None}):
            with self.subTest(change=change):
                report = self.run_report([self.row, {**self.row, **change}])
                self.assertEqual(report["rows"][0]["status"], "unmatched")
                self.assertIn("序號=22598267", report["errors"][0]["error"])
                self.assertIn("不一致", report["errors"][0]["error"])

    def test_missing_ids_and_zero_times_are_invalid(self):
        for change in ({"序號": None}, {"個案序號": None}, {"次數": 0}):
            with self.subTest(change=change):
                report = self.run_report([{**self.row, **change}])
                self.assertEqual(report["summary"]["errors"], 1)
                self.assertEqual(report["rows"][0]["status"], "unmatched")

    def test_appeal_type_any_value_is_true_blank_depends_on_instance(self):
        for value in ("01", "02", "任意非空", 0, 1):
            with self.subTest(value=value):
                result = build_judgment_analysis(self.case, [{**self.row, "上訴類別": value}])
                self.assertIs(result.defendants[0].is_pros_appeal, True)
        for case, expected in ((self.case, False), ("臺灣臺北地方法院115年度審訴字第1號", None),
                               ("臺灣臺北地方法院115年度簡上字第1號", False)):
            with self.subTest(case=case):
                result = build_judgment_analysis(case, [{**self.row, "上訴類別": None}])
                self.assertIs(result.defendants[0].is_pros_appeal, expected)

    def test_sentence_units_rates_and_invalid_months_days(self):
        for raw, expected in ((0, None), (300, 3), (10200, 14), (300000, 360)):
            result = build_judgment_analysis(self.case, [{**self.row, "個案徒刑": raw}])
            self.assertEqual(result.defendants[0].crimes[0].penalty, expected)
        for raw in (1200, 101, -1):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                build_judgment_analysis(self.case, [{**self.row, "個案徒刑": raw}])
        for raw, expected in (("易科二仟折算", 2000), ("易科三仟折算", 3000), (1000, 1000)):
            result = build_judgment_analysis(self.case, [{**self.row, "個案徒刑易科標準": raw}])
            self.assertEqual(result.defendants[0].crimes[0].penalty_to_fine, expected)
        row = {**self.row, "個案罰金": 3000, "個案罰金易勞標準": "易勞一仟折算"}
        result = build_judgment_analysis(self.case, [row])
        self.assertEqual(result.defendants[0].crimes[0].fine, 3000)
        self.assertEqual(result.defendants[0].crimes[0].fine_to_detention, 1000)

    def test_law_code_parts_aliases_and_invalid_codes(self):
        for code, field, expected in (("1051x1A00", "paragraph_part", "A"),
                                      ("0430A0x00", "article_part", "A"),
                                      ("3394x1x02", "paragraph_sub", 2)):
            law = excel_row_to_law_reference({**self.row, "法條": code})
            self.assertEqual(getattr(law, field), expected)
        law = excel_row_to_law_reference({**self.row, "個案案由": "毒品防制條例"})
        self.assertEqual(law.law_name, "毒品危害防制條例")
        for code in ("000000000", 0, None):
            self.assertIsNone(excel_row_to_law_reference({**self.row, "法條": code}))
        for code in ("3394x1x2", "3394Q1x02", "bad"):
            with self.subTest(code=code), self.assertRaises(ValueError):
                excel_row_to_law_reference({**self.row, "法條": code})

    def test_case_rows_span_sheets_and_files(self):
        other = {**self.row, "姓名": "李○○"}
        self.data["defendants"].append({**deepcopy(self.data["defendants"][0]), "name": "李大華"})
        self.source.write_text(json.dumps(self.data), encoding="utf-8")
        with pd.ExcelWriter(self.excel) as writer:
            pd.DataFrame([self.row]).to_excel(writer, sheet_name="一", index=False)
            pd.DataFrame([other]).to_excel(writer, sheet_name="二", index=False)
        self.assertEqual(compare_files(self.excel, self.root)["rows"][0]["status"], "equal")
        pd.DataFrame([self.row]).to_excel(self.excel, index=False)
        pd.DataFrame([other]).to_excel(self.root / "另一份.xlsx", index=False)
        self.assertEqual(compare_files(self.root, self.root)["rows"][0]["status"], "equal")

    def test_different_documents_are_not_combined(self):
        other = deepcopy(self.data)
        other["defendants"][0]["probation"] = 5
        (self.root / "另一份.json").write_text(json.dumps(other), encoding="utf-8")
        result = self.run_report()["rows"][0]
        self.assertEqual(result["status"], "equal")
        self.assertEqual({r["status"] for r in result["document_results"]}, {"equal", "unmatched"})
        self.data["defendants"][0]["crimes"][0]["times"] = 5
        self.assertEqual(self.run_report()["rows"][0]["status"], "unmatched")

    def test_nonguilty_and_exempt_crimes_are_compared(self):
        for status in ("無罪", "免訴", "不受理", "免刑"):
            with self.subTest(status=status):
                row = {**self.row, "個案裁判情形": status, "應執裁判情形": status,
                       "個案徒刑": 0, "應執徒刑": 0, "個案徒刑易科標準": None,
                       "應執徒刑易科標準": None, "法條": "000000000"}
                expected = build_judgment_analysis(self.case, [row])
                actual = expected.model_copy(deep=True)
                actual.defendants[0].name = "陳小明"
                self.assertEqual(compare_analyses(expected, actual)["status"], "equal")

    def test_invalid_case_json_and_missing_headers(self):
        report = self.run_report([self.row, {**self.row, "裁判年度": "無效"}])
        self.assertEqual(report["summary"]["errors"], 1)
        self.assertEqual(report["summary"]["unmatched"], 1)
        del self.data["defendants"][0]["crimes"][0]["laws"]
        report = self.run_report()
        self.assertEqual(report["rows"][0]["reason"], "判決不存在")
        self.assertEqual(report["summary"]["errors"], 1)
        del self.row["個案序號"]
        report = self.run_report()
        self.assertTrue(any("缺少執行明細欄位" in e["error"] for e in report["errors"]))

    def test_report_output_and_source_protection(self):
        self.run_report()
        destination = self.root / "report.json"
        report = compare_to_report(self.excel, self.root, destination)
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), report)
        with self.assertRaisesRegex(ValueError, "覆寫分析 JSON"):
            compare_to_report(self.excel, self.root, self.source)


if __name__ == "__main__":
    unittest.main()
