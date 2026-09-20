"""Offline verification of the actual frozen application."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk

import certifi
import pandas as pd
import xlrd

from judgment_app.gui import CaseReaderApp
from judgment_app.paths import application_dir, default_output_dir
from judgment_app.read_case_numbers import read_case_numbers
from judgment_app.read_judgment_web import save_document


def run(report_path: str, xls_path: str | None = None) -> None:
    report = {"application_dir": str(application_dir()), "output": str(default_output_dir())}
    with TemporaryDirectory() as directory:
        workbook = Path(directory) / "測試案號.xlsx"
        pd.DataFrame([["臺北地院", 114, "訴", 219]]).to_excel(workbook, index=False, header=False)
        cases = read_case_numbers(workbook, "A", "B", "C", "D", start_row=1)
        assert cases == [dict(court="TPD", year=114, case="訴", number=219)]
        report["xlsx"] = True
        if xls_path:
            with pd.ExcelFile(xls_path, engine="xlrd") as book:
                assert book.sheet_names
                book.parse(book.sheet_names[0], nrows=1)
            report["xls"] = True
        else:
            report["xlrd"] = xlrd.__version__
        assert Path(certifi.where()).is_file()
        report["https_certificates"] = True
        path = save_document(directory, "測試判決", ".txt", "全文".encode("utf-8"))
        assert path.read_text(encoding="utf-8") == "全文"
        root = tk.Tk()
        root.withdraw()
        try:
            app = CaseReaderApp(root)
            app.merge_cases(cases)
            root.update()
            assert app.visible_cases() == cases
            report["gui"] = True
        finally:
            for callback in root.tk.call("after", "info"):
                root.after_cancel(callback)
            root.destroy()
    report["success"] = True
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
