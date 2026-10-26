import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_notion_model.py"
spec = importlib.util.spec_from_file_location("sync_notion_model", SCRIPT)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class SyncTests(unittest.TestCase):
    def test_initial_sync_appends(self):
        payload = sync.make_payload("## 人工規則\n保留", "new")
        self.assertEqual(payload["type"], "insert_content")
        self.assertEqual(payload["insert_content"]["position"], {"type": "end"})

    def test_only_managed_section_is_replaced(self):
        old = f"{sync.START}\nold\n{sync.END}"
        page = "人工規則\n\n" + old + "\n\n其他紀錄"
        payload = sync.make_payload(page, "new")
        change = payload["update_content"]["content_updates"][0]
        self.assertEqual(change["old_str"], old)
        self.assertEqual(page.replace(change["old_str"], change["new_str"]),
                         "人工規則\n\nnew\n\n其他紀錄")

    def test_corrupt_markers_fail_closed(self):
        for text in [sync.START, sync.END, sync.END + "\n" + sync.START,
                     sync.START + "\n" + sync.START + "\n" + sync.END]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                sync.make_payload(text, "new")

    def test_same_content_is_noop(self):
        content = f"{sync.START}\nbody\n{sync.END}"
        self.assertIsNone(sync.make_payload(content, content))

    def test_model_is_parsed_without_execution(self):
        source = 'raise RuntimeError("must not execute")\nclass Model:\n    value: int\n'
        text = sync.build_content(source, "abc123")
        self.assertIn("#### Model", text)
        self.assertIn("`value`: `int`", text)
        self.assertIn(source.rstrip(), text)
        self.assertIn("/blob/abc123/", text)

    def test_sync_verifies_content_and_preserves_manual_text(self):
        content = sync.build_content("class M:\n    x: int\n", "abc")
        session = Mock()
        responses = [{"markdown": "Manual"}, {"markdown": "Manual"}, {},
                     {"markdown": "Manual\n" + content}]
        session.request.side_effect = [Mock(json=Mock(return_value=r)) for r in responses]
        sync.sync_page(session, "page", content)
        self.assertEqual(session.request.call_args_list[2].args[0], "PATCH")

    def test_truncated_read_never_writes(self):
        session = Mock()
        session.request.return_value.json.return_value = {"markdown": "partial", "truncated": True}
        with self.assertRaises(RuntimeError):
            sync.sync_page(session, "page", "new")
        session.request.assert_called_once()

    def test_concurrent_edit_aborts(self):
        session = Mock()
        session.request.side_effect = [Mock(json=Mock(return_value={"markdown": s}))
                                       for s in ["Before", "Changed"]]
        with self.assertRaises(RuntimeError):
            sync.sync_page(session, "page", "new")
        self.assertEqual(session.request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
