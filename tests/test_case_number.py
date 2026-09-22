import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from judgment_app.case_number import CaseNumber, JudgmentId
from judgment_app.download_judgments import get_judgment, save_all_judgments
from judgment_app.exceptions import ApiResponseError
from judgment_app.read_judgment_web import case_label, judgment_filename


class CaseNumberTests(unittest.TestCase):
    def test_court_aliases_share_identity_and_filename(self):
        expected = CaseNumber("TPD", 114, "訴", 219)
        for court in ("TPDM", "臺灣臺北地方法院", "台北地院", " TPD "):
            with self.subTest(court=court):
                case = CaseNumber(court, 114, "訴", 219)
                self.assertEqual(case, expected)
                self.assertEqual(hash(case), hash(expected))
        jid = "TPDM,114,訴,219,20250328,1"
        self.assertEqual(CaseNumber.from_jid(jid), expected)
        self.assertEqual(case_label(expected), judgment_filename(jid))
        self.assertEqual(expected.to_dict()["court"], "TPD")

    def test_document_identity_preserves_category_date_version_and_extra_fields(self):
        values = [
            "TPDM,114,訴,219,20250328,1",
            "TPDM,114,訴,219,20250328,2",
            "TPDM,114,訴,219,20250428,1",
            "TPDV,114,訴,219,20250328,1",
            "TPDM,114,訴,219,0,20250328,check",
        ]
        documents = [JudgmentId.from_string(value) for value in values]
        self.assertEqual(len(set(documents)), len(values))
        self.assertEqual([document.raw for document in documents], values)
        self.assertEqual({document.case_number for document in documents}, {CaseNumber("TPD", 114, "訴", 219)})
        self.assertEqual(documents[3].category, "V")
        self.assertEqual(documents[-1].suffix, ("0", "20250328", "check"))

    def test_invalid_court_is_not_silently_truncated(self):
        for court in ("TPDX", "TPDMM", "UNKNOWN"):
            with self.subTest(court=court), self.assertRaises(ValueError):
                CaseNumber(court, 114, "訴", 219)
        for jid in ("TPDM,114,訴,219", "TPDM,no,訴,219,20250328,1", "TPDM,114,訴,,20250328,1"):
            with self.subTest(jid=jid), self.assertRaises(ValueError):
                JudgmentId.from_string(jid)

    def test_original_case_does_not_change_case_identity(self):
        original = CaseNumber("TPD", 114, "訴", 219)
        case = CaseNumber.from_jid("TPHM,114,上訴,30,20250328,1", original_case=original)
        self.assertIs(case.original_case, original)
        self.assertEqual(case, CaseNumber("TPH", 114, "上訴", 30))

    def test_api_keeps_exact_jid_and_rejects_another_document(self):
        jid = "TPDM,114,訴,219,20250328,1"
        data = dict(JID=jid, JYEAR=114, JCASE="訴", JNO=219, JDATE="20250328", JTITLE="測試", JFULLX={"JFULLCONTENT": "正文"})
        with patch("judgment_app.download_judgments.post_json", return_value=data) as post:
            self.assertEqual(get_judgment("token", jid), data)
            self.assertEqual(post.call_args.args[1]["j"], jid)
        with patch("judgment_app.download_judgments.post_json", return_value=data | {"JID": jid[:-1] + "2"}):
            with self.assertRaises(ApiResponseError):
                get_judgment("token", jid)

    def test_bulk_download_filters_category_and_records_invalid_jid(self):
        criminal = "TPDM,114,訴,219,20250328,1"
        civil = "TPDV,114,訴,219,20250328,1"
        with TemporaryDirectory() as folder, patch("judgment_app.download_judgments.get_jid_json", return_value={"date": "20250328", "list": [criminal, civil, "invalid"]}), patch("judgment_app.download_judgments.get_judgment", return_value={"text": "正文"}) as get:
            save_all_judgments("token", Path(folder))
            get.assert_called_once_with("token", criminal)
            errors = json.loads((Path(folder) / "20250328" / "except.json").read_text())
            self.assertIn("invalid", errors)
