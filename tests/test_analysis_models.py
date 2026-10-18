import unittest

from pydantic import ValidationError

from judgment_app.analysis.analyzer import _decode, _schema
from judgment_app.analysis.models import JudgmentAnalysis, LawReference


class AnalysisModelsTests(unittest.TestCase):
    def payload(self):
        return {
            "case_number": "測試案號",
            "defendants": [{
                "name": "王某", "is_recidivist": False,
                "crimes": [{
                    "crime_id": 1, "crime_jsst": "科刑", "crime_name": "測試罪名",
                    "times": 1, "penalty": 6,
                    "laws": [{"law_name": "毒品防制條例", "article": 4,
                              "is_attempted": False, "is_accessory": False}],
                }],
                "execution_groups": [{"crime_ids": [1], "executed_jsst": "科刑",
                                      "executed_penalty": 6}],
            }],
        }

    def test_nested_round_trip_and_analyzer_compatibility(self):
        result = JudgmentAnalysis.from_dict(self.payload())
        law = result.defendants[0].crimes[0].laws[0]
        self.assertIsInstance(law, LawReference)
        self.assertEqual(law.law_name, "毒品危害防制條例")
        data = result.to_dict()
        self.assertEqual(data, result.model_dump())
        self.assertIsNone(data["lower_court_case_number"])
        self.assertEqual(_decode(JudgmentAnalysis, data), result)
        self.assertEqual(JudgmentAnalysis.from_dict(data), result)
        with self.assertRaises(ValidationError):
            law.article = 5
        self.assertEqual(len({law, law}), 1)

    def test_strict_nested_types_and_unknown_fields(self):
        for field, value in (("times", "1"), ("times", True), ("crime_name", 3),
                             ("unexpected", 1), ("penalty", 0)):
            with self.subTest(field=field, value=value):
                data = self.payload()
                data["defendants"][0]["crimes"][0][field] = value
                with self.assertRaises(ValueError):
                    JudgmentAnalysis.from_dict(data)
        with self.assertRaises(ValidationError):
            LawReference(law_name="刑法", article="1", is_attempted=False, is_accessory=False)

    def test_required_fields_and_business_rules(self):
        for change in (lambda d: d.pop("crimes"),
                       lambda d: d.update(name="  "),
                       lambda d: d["execution_groups"][0].update(crime_ids=[1, 1])):
            data = self.payload()
            change(data["defendants"][0])
            with self.assertRaises((TypeError, ValueError)):
                JudgmentAnalysis.from_dict(data)

    def test_response_requires_nullable_fields_at_every_level(self):
        result = JudgmentAnalysis.model_validate(self.payload())
        for path, field in (((), "lower_court_case_number"),
                            (("defendants", 0), "confiscation"),
                            (("defendants", 0, "crimes", 0), "fine"),
                            (("defendants", 0, "crimes", 0, "laws", 0), "paragraph")):
            data = result.model_dump()
            container = data
            for key in path:
                container = container[key]
            del container[field]
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, field):
                _decode(JudgmentAnalysis, data)
            JudgmentAnalysis.from_dict(data)

    def test_law_constraints_and_error_location(self):
        for field, value in (("article_sub", 0), ("paragraph", -1),
                             ("article_part", "前段"), ("paragraph_part", "D"),
                             ("is_attempted", 1)):
            data = self.payload()
            data["defendants"][0]["crimes"][0]["laws"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError) as caught:
                JudgmentAnalysis.model_validate(data)
            self.assertEqual(caught.exception.errors()[0]["loc"],
                             ("defendants", 0, "crimes", 0, "laws", 0, field))

    def test_schema_retains_descriptions_and_required_nullable_fields(self):
        schema = _schema(JudgmentAnalysis)
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("lower_court_case_number", schema["required"])
        self.assertTrue(schema["properties"]["case_number"]["description"])
        self.assertIn({"type": "null"},
                      schema["properties"]["lower_court_case_number"]["anyOf"])


if __name__ == "__main__":
    unittest.main()
