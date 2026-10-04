import os
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from judgment_app.analysis.analyze_judgment import analyze_folder
from judgment_app.gui import CaseReaderApp
from judgment_app.analysis.analyze_judgment import _case_folder_jobs
import json


class FolderAnalysisTests(unittest.TestCase):
    def test_case_folder_targets_multiple_judgments_and_uses_history_only_as_context(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory) / "臺灣高等法院115年度上訴字第1299號"
            folder.mkdir()
            first = folder / "臺灣高等法院115年度上訴字第1299號_20260101_1.json"
            first.write_text(json.dumps({"jid": "TPHM,115,上訴,1299,20260101,1", "text": "目標一"}))
            first.with_suffix(".txt").write_text("目標一")
            first.with_name(first.name + ".analysis.json").write_text('{"defendants": []}')
            second = folder / "臺灣高等法院115年度上訴字第1299號_20260201_1.txt"
            second.write_text("目標二")
            history = folder / "臺灣臺北地方法院114年度訴字第12號_20250101_1.txt"
            history.write_text("歷審全文")
            jobs = _case_folder_jobs(folder)
            self.assertEqual([job[0] for job in jobs], [first, second])
            for source, target, references in jobs:
                self.assertEqual(target, folder.name)
                self.assertEqual(len(references), 2)
                self.assertIn("歷審全文", [item["judgment"] for item in references])
                self.assertNotIn(source.name, [item["source"] for item in references])

    def test_missing_target_does_not_analyze_history(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory) / "臺灣高等法院115年度上訴字第1299號"
            folder.mkdir()
            (folder / "臺灣臺北地方法院114年度訴字第12號.txt").write_text("歷審全文")
            with patch("judgment_app.analysis.analyze_judgment.analyze_judgment") as analyze:
                report = analyze_folder(directory)
            analyze.assert_not_called()
            self.assertEqual(len(report["errors"]), 1)
            self.assertIn("找不到符合", report["errors"][0])

    def test_recursive_batch_continues_after_failure(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            
            for name in ("a.TXT", "b.json", "c.txt", "skip.pdf"):
                (folder / name).write_text("test", encoding="utf-8")
            progress, log = Mock(), Mock()
            with patch("judgment_app.analysis.analyze_judgment.load_settings"), patch.dict(
                os.environ, {"OPENAI_API_KEY": "test"}
            ), patch("judgment_app.analysis.analyze_judgment.analyze_judgment",
                     side_effect=[None, ValueError("bad JSON"), None]) as analyze:
                report = analyze_folder(folder, progress=progress, on_result=log)
            self.assertEqual(report["total"], 3)
            self.assertEqual(len(report["files"]), 2)
            self.assertIn("bad JSON", report["errors"][0])
            self.assertEqual(progress.call_count, 3)
            self.assertEqual(log.call_count, 3)
            for call in analyze.call_args_list:
                self.assertEqual(call.args[0].parent, folder.resolve())

    def test_empty_folder_and_missing_credentials(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "old.txt.analysis.json").write_text('{"defendants": []}')
            self.assertEqual(analyze_folder(folder)["total"], 0)
            (folder / "a.txt").write_text("text")
            with patch("judgment_app.analysis.analyze_judgment.load_settings"), patch.dict(
                os.environ, {"OPENAI_API_KEY": ""}
            ), self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                analyze_folder(folder)
            self.assertFalse((folder / "a.txt.analysis.json").exists())


class GuiAnalysisTests(unittest.TestCase):
    def app(self):
        app = CaseReaderApp.__new__(CaseReaderApp)
        app.busy = False
        app.root = Mock()
        app.output = Mock()
        app.analysis_provider = Mock()
        app.analysis_provider.get.return_value = "gemini"
        app.status = Mock()
        app.events = Queue()
        app.run_task = Mock()
        app.append_log = Mock()
        app.set_busy = Mock()
        return app

    def test_start_uses_background_task_and_queue(self):
        app = self.app()
        with TemporaryDirectory() as directory:
            app.output.get.return_value = directory
            app.start_analysis()
            kind, operation = app.run_task.call_args.args
            self.assertEqual(kind, "analysis")
            with patch("judgment_app.gui.analyze_folder") as analyze:
                operation()
                self.assertEqual(analyze.call_args.args[0], Path(directory))
                self.assertEqual(analyze.call_args.kwargs["provider"], "gemini")
                analyze.call_args.kwargs["progress"]("working")
                analyze.call_args.kwargs["on_result"]("saved")
            self.assertEqual(app.events.get_nowait(), ("progress", "working", None))
            self.assertEqual(app.events.get_nowait(), ("result", "saved", None))

    def test_busy_and_invalid_path_do_not_start(self):
        app = self.app()
        app.busy = True
        app.start_analysis()
        app.run_task.assert_not_called()
        app.busy = False
        app.output.get.return_value = ""
        with patch("judgment_app.gui.messagebox.showerror") as error:
            app.start_analysis()
            error.assert_called_once()
        app.run_task.assert_not_called()

    def test_completion_restores_controls_and_logs_outputs(self):
        app = self.app()
        app.events.put(("analysis", {"total": 2, "files": ["a.txt"],
                                    "errors": ["failed"], "outputs": ["a.txt.analysis.json"]}, None))
        app.poll()
        self.assertIn("成功 1 份，失敗 1 份", app.status.set.call_args.args[0])
        app.append_log.assert_any_call("JSON：a.txt.analysis.json")
        app.set_busy.assert_called_once_with(False)


if __name__ == "__main__":
    unittest.main()
