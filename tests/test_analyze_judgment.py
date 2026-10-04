from dataclasses import asdict, fields
import json
from copy import deepcopy
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import requests

from judgment_app.analysis.analyze_judgment import analyze_judgment, read_judgment
from judgment_app.analysis.analysis_result import (
    CrimeAnalysis, DefendantAnalysis, ExecutionGroup, JudgmentAnalysis, LawReference,
)


class AnalyzeJudgmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "判決.json"
        self.source.write_text(json.dumps({"JID": "test-jid", "JFULLX": {
            "JFULLCONTENT": "判決全文測試"}}, ensure_ascii=False), encoding="utf-8-sig")
        self.output = self.root / "results.json"
        law = LawReference("刑法", 339, paragraph=1, paragraph_sub=1)
        crime = CrimeAnalysis(1, "測試罪名", False, False, [law], 1, 50,
                              None, None, None, None, None, None, None)
        defendant = DefendantAnalysis(
            name="王某", appeal_result=None, is_recidivist=False, crimes=[crime],
            execution_groups=[ExecutionGroup(crime_ids=[1], executed_penalty=62, executed_penalty_to_fine=None,
            executed_detention=None, executed_detention_to_fine=None,
            executed_fine=None, executed_fine_to_detention=None,
            executed_addition_fine=None, executed_addition_fine_to_detention=None)],
            probation=None, probation_law=None, confiscation=["1000元"],
        )
        self.result = {"jid": "test-jid", "lower_court_case_number": None,
                       "defendants": [asdict(defendant)], "is_pros_appeal": None,
                       "pros_appearing": ["陳某"]}
        self.client = Mock()
        self.response = self.client.post.return_value
        self.response.json.return_value = self.payload()
        self.settings = patch("judgment_app.analysis.analyze_judgment.load_settings")
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def payload(self):
        return {"id": "resp_test", "status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text",
             "text": json.dumps(self.result, ensure_ascii=False)}]}]}

    def run_analysis(self, **kwargs):
        return analyze_judgment(self.source, self.output, session=self.client, **kwargs)

    def test_success_round_trip_and_replace(self):
        result = self.run_analysis(model="test-model")
        self.assertEqual(asdict(result), self.result)
        self.assertIsInstance(result.defendants[0].crimes[0], CrimeAnalysis)
        self.run_analysis(model="test-model")
        self.assertEqual(json.loads(self.output.read_text()), self.result)
        body = self.client.post.call_args.kwargs["json"]
        schema = body["text"]["format"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        crime_fields = schema["properties"]["defendants"]["items"]["properties"]["crimes"]["items"]["properties"]
        self.assertIn("徒刑月數", crime_fields["penalty"]["description"])
        self.assertIn("一審為地方法院", body["instructions"])
        self.assertEqual(json.loads(body["input"])["judgment"], "判決全文測試")

    def test_every_output_field_has_python_description(self):
        classes = (JudgmentAnalysis, DefendantAnalysis, ExecutionGroup, CrimeAnalysis, LawReference)
        for data_class in classes:
            for output_field in fields(data_class):
                with self.subTest(data_class=data_class.__name__, field=output_field.name):
                    self.assertTrue(output_field.metadata.get("description"))

    def test_target_and_history_are_sent_in_separate_fields(self):
        references = [{"jid": "history", "judgment": "歷審全文"}]
        self.run_analysis(target_case_number="臺灣高等法院115年度上訴字第1299號",
                          reference_documents=references)
        body = self.client.post.call_args.kwargs["json"]
        content = json.loads(body["input"])
        self.assertEqual(content["reference_documents"], references)
        self.assertEqual(content["judgment"], "判決全文測試")
        self.assertIn("不得另輸出參考判決", body["instructions"])
        self.assertEqual(json.loads(self.output.read_text()), self.result)

    def test_invalid_numeric_encodings_do_not_persist(self):
        defendant = self.result["defendants"][0]
        crime = defendant["crimes"][0]
        for container, field, value in (
            (defendant, "probation_law", 3), (defendant, "probation", -1),
            (defendant["execution_groups"][0], "executed_penalty", 361),
            (defendant["execution_groups"][0], "executed_penalty", -1),
            (defendant["execution_groups"][0], "executed_fine_to_detention", -1000),
            (crime, "penalty", -1), (crime, "detention", -1),
            (crime, "fine", -1000),
            (crime, "times", 0), (crime, "times", -1),
            (crime, "times", True), (crime, "times", 1.5),
        ):
            original = container[field]
            container[field] = value
            self.response.json.return_value = self.payload()
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.run_analysis()
            self.assertFalse(self.output.exists())
            container[field] = original

    def test_groups_and_crime_references_survive_merge(self):
        defendant = self.result["defendants"][0]
        duplicate = deepcopy(defendant["crimes"][0])
        duplicate["crime_id"] = 2
        duplicate["times"] = 2
        other = deepcopy(duplicate)
        other["crime_id"] = 3
        defendant["crimes"].extend([duplicate, other])
        defendant["execution_groups"][0]["crime_ids"] = [1, 2]
        second = deepcopy(defendant["execution_groups"][0])
        second["crime_ids"] = [3]
        second["executed_penalty_to_fine"] = 2000
        defendant["execution_groups"].append(second)
        self.response.json.return_value = self.payload()
        result = self.run_analysis()
        self.assertEqual(len(result.defendants), 1)
        self.assertEqual([c.times for c in result.defendants[0].crimes], [3, 2])
        self.assertEqual([g.crime_ids for g in result.defendants[0].execution_groups], [[1], [3]])
        self.assertEqual(json.loads(self.output.read_text()), asdict(result))

    def test_invalid_group_references(self):
        group = self.result["defendants"][0]["execution_groups"][0]
        for ids in ([], [99], [1, 1], [True]):
            group["crime_ids"] = ids
            self.response.json.return_value = self.payload()
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.run_analysis()
            self.assertFalse(self.output.exists())

    def test_empty_execution_groups(self):
        self.result["defendants"][0]["execution_groups"] = []
        self.response.json.return_value = self.payload()
        self.assertEqual(self.run_analysis().defendants[0].execution_groups, [])

    def test_prompt_uses_current_field_rules(self):
        self.run_analysis()
        prompt = self.client.post.call_args.kwargs["json"]["instructions"]
        self.assertIn("地方法院判決且案號無", prompt)
        schema = self.client.post.call_args.kwargs["json"]["text"]["format"]["schema"]
        properties = schema["properties"]
        defendant = properties["defendants"]["items"]["properties"]
        self.assertIn("同一被告一筆", properties["defendants"]["description"])
        self.assertIn("一審蒞庭檢察官", properties["pros_appearing"]["description"])
        self.assertIn("無罪仍列被訴罪名", defendant["crimes"]["description"])
        self.assertIn("應執行刑分組", defendant["execution_groups"]["description"])

    def test_rules_are_read_from_model_module_for_both_providers(self):
        for provider in ("openai", "gemini"):
            if provider == "gemini":
                self.response.json.return_value = {"candidates": [{"finishReason": "STOP",
                    "content": {"parts": [{"text": json.dumps(self.result)}]}}]}
            with patch("judgment_app.analysis.analysis_result.ANALYSIS_RULES", "新版規則"), patch.dict(
                os.environ, {"GEMINI_API_KEY": "test"}
            ):
                self.run_analysis(provider=provider)
            body = self.client.post.call_args.kwargs["json"]
            prompt = body["instructions"] if provider == "openai" else body["systemInstruction"]["parts"][0]["text"]
            self.assertIn("新版規則", prompt)
            schema = (body["text"]["format"]["schema"] if provider == "openai"
                      else body["generationConfig"]["responseJsonSchema"])
            defendant = schema["properties"]["defendants"]["items"]["properties"]
            self.assertIn("4年2月為50", defendant["crimes"]["items"]["properties"]["penalty"]["description"])
            self.assertIn("刑期乘times", defendant["execution_groups"]["description"])

    def test_invalid_law_values_do_not_overwrite_existing_output(self):
        self.run_analysis()
        original_output = self.output.read_text()
        law = self.result["defendants"][0]["crimes"][0]["laws"][0]
        for field, value in (("article", 0), ("article_sub", -1), ("paragraph", 0),
                             ("subparagraph", 0), ("part", "前段"), ("law_name", " ")):
            original = law[field]
            law[field] = value
            self.response.json.return_value = self.payload()
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.run_analysis()
            self.assertEqual(self.output.read_text(), original_output)
            law[field] = original

    def test_executed_fields_have_schema_descriptions_and_allow_null(self):
        for field in self.result["defendants"][0]["execution_groups"][0]:
            if field.startswith("executed_"):
                self.result["defendants"][0]["execution_groups"][0][field] = None
        self.response.json.return_value = self.payload()
        self.run_analysis()
        schema = self.client.post.call_args.kwargs["json"]["text"]["format"]["schema"]
        defendant = schema["properties"]["defendants"]["items"]["properties"]["execution_groups"]["items"]
        fields = [name for name in defendant["properties"] if name.startswith("executed_")]
        self.assertEqual(len(fields), 8)
        for field in fields:
            self.assertIn(field, defendant["required"])
            self.assertIn("應執行", defendant["properties"][field]["description"])
        self.assertEqual(json.loads(self.output.read_text()), self.result)

    def test_gemini_success_and_request(self):
        self.response.json.return_value = {"responseId": "gemini-response", "candidates": [
            {"finishReason": "STOP", "content": {"parts": [
                {"text": "reasoning", "thought": True}, {"text": json.dumps(self.result)}]}}]}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-key", "OPENAI_API_KEY": ""}):
            result = self.run_analysis(provider="gemini", model="gemini-2.5-flash")
        self.assertEqual(asdict(result), self.result)
        call = self.client.post.call_args
        self.assertIn("generativelanguage.googleapis.com", call.args[0])
        self.assertEqual(call.kwargs["headers"], {"x-goog-api-key": "gemini-key"})
        self.assertIn("responseJsonSchema", call.kwargs["json"]["generationConfig"])
        self.assertEqual(json.loads(self.output.read_text()), self.result)

    def test_gemini_incomplete_and_blocked_do_not_persist(self):
        for payload in ({"promptFeedback": {"blockReason": "SAFETY"}},
                        {"candidates": [{"finishReason": "MAX_TOKENS"}]},
                        {"candidates": [{"finishReason": "STOP", "content": {"parts": []}}]}):
            self.response.json.return_value = payload
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test"}), self.assertRaises(ValueError):
                self.run_analysis(provider="gemini")
            self.assertFalse(self.output.exists())

    def test_gemini_missing_key_and_unknown_provider(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}), self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
            self.run_analysis(provider="gemini")
        with self.assertRaisesRegex(ValueError, "不支援"):
            self.run_analysis(provider="unknown")
        self.client.post.assert_not_called()

    def test_default_output_and_failed_rerun_preserves_json(self):
        analyze_judgment(self.source, session=self.client)
        output = self.source.with_name(self.source.name + ".analysis.json")
        original = output.read_text(encoding="utf-8")
        self.assertEqual(json.loads(original), self.result)
        self.response.json.return_value = {"status": "incomplete"}
        with self.assertRaises(ValueError):
            analyze_judgment(self.source, session=self.client)
        self.assertEqual(output.read_text(encoding="utf-8"), original)

    def test_txt_and_web_json(self):
        source = self.root / "test.txt"
        source.write_text("判決全文", encoding="utf-8-sig")
        self.assertEqual(read_judgment(source), ("", "判決全文"))
        self.source.write_text(json.dumps({"jid": "jid2", "text": "全文"}), encoding="utf-8")
        self.assertEqual(read_judgment(self.source), ("jid2", "全文"))

    def test_invalid_input_before_api(self):
        for value in ([], {}, {"text": " "}, {"jid": 42, "text": "全文"}):
            with self.subTest(value=value):
                self.source.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.run_analysis()
        self.client.post.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_incomplete_refusal_and_missing_text_do_not_persist(self):
        for payload in (
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
            {"status": "completed", "output": [{"content": [{"type": "refusal", "refusal": "拒絕"}]}]},
            {"status": "completed", "output": []},
        ):
            with self.subTest(payload=payload):
                self.response.json.return_value = payload
                with self.assertRaises(ValueError):
                    self.run_analysis()
                self.assertFalse(self.output.exists())

    def test_wrong_types_fields_and_jid_do_not_persist(self):
        for field, value in (("jid", "wrong"), ("is_pros_appeal", 1), ("defendants", {})):
            original = self.result[field]
            self.result[field] = value
            self.response.json.return_value = self.payload()
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.run_analysis()
            self.result[field] = original
        self.result["extra"] = "unexpected"
        self.response.json.return_value = self.payload()
        with self.assertRaises(ValueError):
            self.run_analysis()
        self.assertFalse(self.output.exists())

    def test_http_error_preserves_context(self):
        original = requests.HTTPError("Unauthorized")
        self.response.raise_for_status.side_effect = original
        self.response.status_code = 401
        self.response.text = "invalid API key"
        with self.assertRaises(RuntimeError) as caught:
            self.run_analysis()
        self.assertIs(caught.exception.__cause__, original)
        self.assertFalse(self.output.exists())

    def test_missing_key_and_input_as_output(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                self.run_analysis()
        with self.assertRaisesRegex(ValueError, "不可與輸入檔相同"):
            analyze_judgment(self.source, self.source)
        self.client.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
