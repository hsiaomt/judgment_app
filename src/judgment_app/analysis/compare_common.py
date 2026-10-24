"""Excel 比對共用的欄位轉換、姓名配對及檔案讀寫工具。"""

import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

import pandas as pd
from openpyxl import Workbook, load_workbook

from judgment_app.analysis.analysis_models import JudgmentAnalysis
from judgment_app.case_number import CaseNumber

IDENTITY_COLUMNS = {"裁判機關", "裁判年度", "裁判冠字", "裁判號數", "姓名"}


def write_excel_statuses(path: Path, statuses: dict, *, legacy_tables=None) -> Path:
    """xlsx 原檔回寫；xls 以讀取的表格另存 xlsx，不覆寫既有結果檔。"""
    destination = path
    if legacy_tables is None:
        workbook = load_workbook(path)
    else:
        destination = path.with_name(f"{path.stem}.status.xlsx")
        number = 2
        while destination.exists():
            destination = path.with_name(f"{path.stem}.status-{number}.xlsx")
            number += 1
        workbook = Workbook()
        workbook.remove(workbook.active)
    temporary = None
    try:
        if legacy_tables is not None:
            for sheet, table in legacy_tables.items():
                worksheet = workbook.create_sheet(sheet)
                for row_number, row in enumerate(table.itertuples(index=False, name=None), 1):
                    for column, value in enumerate(row, 1):
                        if not pd.isna(value):
                            cell = worksheet.cell(row_number, column, value)
                            if isinstance(value, str):
                                cell.data_type = "s"
        for sheet, (header, row_statuses) in statuses.items():
            worksheet = workbook[sheet]
            for field in ("status", "reason"):
                column = next((cell.column for cell in worksheet[header]
                               if _text(cell.value) == field), worksheet.max_column + 1)
                worksheet.cell(header, column, field)
                for row_number in range(header + 1, worksheet.max_row + 1):
                    value = row_statuses.get(row_number, {}).get(field)
                    cell = worksheet.cell(row_number, column)
                    cell.value = value
                    if isinstance(value, str):
                        cell.data_type = "s"
        with NamedTemporaryFile(dir=path.parent, prefix="~$status-", suffix=".xlsx", delete=False) as file:
            temporary = Path(file.name)
        workbook.save(temporary)
        temporary.replace(destination)
        return destination
    finally:
        workbook.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def matches_defendant_name(excel_name: str, json_name: str) -> bool:
    """遮蔽姓名依 ○ 前的任意非空前綴配對，保留遮蔽長度檢查；未遮蔽姓名完整比對。"""
    excel_name = _text(excel_name)
    json_name = _text(json_name)
    if "○" not in excel_name:
        return bool(excel_name) and excel_name == json_name
    excel_prefix, separator, excel_suffix = excel_name.partition("○")
    excel_suffix = separator + excel_suffix
    if not excel_prefix:
        return False
    json_suffix = json_name.split(excel_prefix, 1)[1] if excel_prefix in json_name else ""
    if len(excel_suffix) > len(json_suffix):
        return False
    return json_name.startswith(excel_prefix)


def excel_col_to_val(column: str, value):
    text = _text(value)
    if column in {"未遂", "幫助", "累犯"}:
        bool_values = {
            "": False, "0": False, "0.0": False,
            "否": False, "n": False, "false": False,
            "1": True, "1.0": True,
            "是": True, "y": True, "true": True, "✓": True,
        }
        try:
            return bool_values[text.lower()]
        except KeyError:
            raise ValueError(f"{column}布林值無法辨識：{text}") from None
    if not text or text.lower() == "x":
        return None
    if column in {"裁判情形", "案由"}:
        return text
    if column in {"條段", "項段"}:
        return text.upper()

    number = _non_negative_integer(value)
    if column == "徒刑(YYMM)":
        if number == 0:
            return None
        years, months = divmod(number, 100)
        if months >= 12:
            raise ValueError(f"YYMM 月份不合法：{value}")
        return years * 12 + months
    if number == 0 and column in {"拘役", "罰金", "併科罰金"}:
        return None

    return number


def load_analyses(path: Path, errors: list) -> dict[CaseNumber, list[tuple[Path, JudgmentAnalysis]]]:
    index = {}
    for source in find_files(path, {".json"}):
        try:
            data = json.loads(source.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or "case_number" not in data:
                continue
            analysis = JudgmentAnalysis.from_dict(data)
            case_number = CaseNumber.from_string(analysis.case_number)
            index.setdefault(case_number, []).append((source, analysis))
        except (ValueError, TypeError, KeyError, OSError) as exc:
            errors.append({"file": str(source), "error": f"{type(exc).__name__}: {exc}"})
    return index


def find_files(path: Path, suffixes: set[str]) -> list[Path]:
    if not path.exists():
        raise ValueError(f"路徑不存在：{path}")
    return sorted(p for p in ([path] if path.is_file() else path.rglob("*"))
                  if p.is_file() and p.suffix.lower() in suffixes and not p.name.startswith("~$"))


def _non_negative_integer(value) -> int:
    number = float(value)
    if not number.is_integer() or number < 0:
        raise ValueError(f"非負整數格式錯誤：{value}")
    return int(number)


def _text(value) -> str:
    return "" if _is_blank(value) else re.sub(
        r"\s+", "", str(value)).replace("（", "(").replace("）", ")"
    )


def _is_blank(value) -> bool:
    return value is None or bool(pd.isna(value)) or str(value).strip() == ""
