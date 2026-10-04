import unittest

from judgment_app.analysis.analysis_result import LawReference


class LawNameNormalizationTests(unittest.TestCase):
    def test_names_are_normalized_on_construction_and_from_dict(self):
        for raw, expected in (
            ('詐欺', '刑法'), ('個人資料保護', '個人資料保護法'),
            ('詐欺犯罪防制條例', '詐欺犯罪危害防制條例'),
            ('毒品防制條例', '毒品危害防制條例'), ('洗錢防制法', '洗錢防制法'),
        ):
            with self.subTest(raw=raw):
                law = LawReference(raw, 1)
                self.assertEqual(law, LawReference(expected, 1))
                self.assertEqual(law.to_dict()['law_name'], expected)
                self.assertEqual(LawReference.from_dict({'law_name': raw, 'article': 1}), law)

    def test_missing_name_raises_value_error(self):
        for name in (None, '', ' '):
            with self.subTest(name=name), self.assertRaises(ValueError):
                LawReference(name, 1)
