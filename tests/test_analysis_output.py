import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from judgment_app.analysis.analyzer import analyze_source, analyze_folder
from judgment_app.analysis.compare import compare_to_report


class AnalysisOutputTests(unittest.TestCase):
    def test_single_file_output_and_failure_summary(self):
        with TemporaryDirectory() as directory:
            source = Path(directory).resolve() / 'case.txt'
            source.write_text('全文')
            destination = Path(directory).resolve() / 'outputs'
            with patch('judgment_app.analysis.analyzer.analyze_judgment') as analyze:
                report = analyze_source(source, destination, provider='gemini')
                analyze.assert_called_once_with(source, destination / 'case.txt.analysis.json', provider='gemini')
                self.assertEqual(report['files'], [str(source)])
            with patch('judgment_app.analysis.analyzer.analyze_judgment', side_effect=ValueError('回應錯誤')):
                report = analyze_source(source, destination)
                self.assertEqual(report['outputs'], [])
                self.assertIn('ValueError: 回應錯誤', report['errors'][0])

    def test_folder_keeps_relative_paths_and_context(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            case = root / '臺灣臺北地方法院114年度訴字第1號'
            case.mkdir()
            source = case / (case.name + '.txt')
            source.write_text('判決全文')
            output = root / 'results'
            output.mkdir()
            (output / 'old.analysis.json').write_text('{}')
            progress = Mock()
            with patch('judgment_app.analysis.analyzer.load_settings'), patch('judgment_app.analysis.analyzer._provider_settings'), patch('judgment_app.analysis.analyzer.analyze_judgment') as analyze:
                report = analyze_folder(root, output_dir=output, progress=progress)
            self.assertEqual(report['total'], 1)
            self.assertEqual(report['errors'], [])
            options = analyze.call_args.kwargs
            self.assertEqual(options['target_case_number'], case.name)
            self.assertEqual(options['output_path'], output / case.name / (source.name + '.analysis.json'))
            self.assertEqual(options['reference_documents'], [])
            progress.assert_called_once()

    def test_report_cannot_overwrite_json_input(self):
        with TemporaryDirectory() as directory:
            source = Path(directory).resolve() / 'analysis.json'
            original = json.dumps({'case_number': 'test'})
            source.write_text(original)
            for input_path in (source, source.parent):
                with self.assertRaisesRegex(ValueError, '覆寫'):
                    compare_to_report(Path(directory).resolve() / 'source.xlsx', input_path, source)
                self.assertEqual(source.read_text(), original)
