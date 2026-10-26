"""比對檢核 Excel 與分析 JSON；以 --help 查看用法。"""

import argparse
import json
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from judgment_app.comparison.models import ComparisonReport, ComparisonResult
from judgment_app.analysis.models import CrimeAnalysis, JudgmentAnalysis, LawReference
from judgment_app.comparison.common import (
    IDENTITY_COLUMNS, excel_bool, excel_integer, excel_optional_text, _non_negative_integer,
    find_excel_files, read_excel_header, iter_excel_rows,
    compare_to_report, load_analyses, matches_defendant_name, _text, write_excel_statuses,
)
from judgment_app.case_number import CaseNumber
from judgment_app.paths import resolve_output_path

# 同名欄位以第一欄為準：Excel 後面的拘役等欄位屬法定刑上下限。
FIELD_MAP = {
    "裁判情形": "crime_jsst", "徒刑(YYMM)": "penalty", "拘役": "detention", "罰金": "fine",
    "累犯": "is_recidivist", "案由": "law_name", 
    "條": "article", "之": "article_sub", "條段": "article_part",
    "項": "paragraph", "項段": "paragraph_part", "款": "paragraph_sub",
    "未遂": "is_attempted", "幫助": "is_accessory",
}
LAW_FIELDS = {"law_name", "article", "article_sub", "article_part", "paragraph", "paragraph_part", "paragraph_sub", "is_attempted", "is_accessory"}
PENALTIES = {"crime_jsst", "penalty", "detention", "fine"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--excel", default="data", help="Excel 檔案或資料夾（預設 data）")
    parser.add_argument("--json-dir", default="/Users/hsiaomt/Desktop/Judgments_101資料夾_最新格式", help="分析 JSON 檔案或資料夾，遞迴搜尋")
    parser.add_argument("--output", default="data/comparison_report.json")
    args = parser.parse_args()
    destination = resolve_output_path(args.output)
    report = compare_to_report(
        resolve_output_path(args.excel), resolve_output_path(args.json_dir), destination,
        compare=compare_files,
    )
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"比對報告：{destination}")
    for output in report["excel_outputs"]:
        print(f"Excel 比對結果：{output['output']}")


def compare_files(excel_path: Path, json_path: Path, *, progress=None, excel_files: list[Path] | None = None) -> dict:
    """回傳每列差異、未配對原因及未比較欄位，並將 status、reason 寫入 Excel。"""
    report = {"rows": [], "errors": [], "sheets": [], "excel_outputs": []}
    if progress:
        progress("正在讀取分析 JSON…")
    index = load_analyses(Path(json_path), report["errors"])
    files = find_excel_files(Path(excel_path)) if excel_files is None else excel_files
    for file_index, path in enumerate(files, 1):
        if progress:
            progress(f"正在比對 Excel {file_index} / {len(files)}：{path.name}")
        statuses = {}
        legacy_tables = {} if path.suffix.lower() == ".xls" else None
        unread_sheets = []
        try:
            book = pd.ExcelFile(path)
        except Exception as exc:
            report["errors"].append({"file": str(path), "stage": "read_workbook",
                                     "error": f"{type(exc).__name__}: {exc}"})
            continue
        try:
            for sheet in book.sheet_names:
                if progress:
                    progress(f"正在比對 {path.name}／{sheet}")
                try:
                    table = book.parse(sheet, header=None, dtype=object)
                except Exception as exc:
                    unread_sheets.append(sheet)
                    report["errors"].append({"file": str(path), "sheet": sheet, "stage": "read_sheet",
                                             "error": f"{type(exc).__name__}: {exc}"})
                    continue
                if legacy_tables is not None:
                    legacy_tables[sheet] = table
                try:
                    rows, sheet_report, sheet_statuses, errors = _compare_sheet(table, path, sheet, index)
                except Exception as exc:
                    report["errors"].append({"file": str(path), "sheet": sheet, "stage": "compare_sheet",
                                             "error": f"{type(exc).__name__}: {exc}"})
                    continue
                report["rows"].extend(rows)
                report["sheets"].append(sheet_report)
                report["errors"].extend(errors)
                if sheet_statuses[1] or not errors:
                    statuses[sheet] = sheet_statuses
        finally:
            try:
                book.close()
            except Exception as exc:
                report["errors"].append({"file": str(path), "stage": "close_workbook",
                                         "error": f"{type(exc).__name__}: {exc}"})
        if legacy_tables is not None and unread_sheets:
            report["errors"].append({"file": str(path), "stage": "write_excel",
                                     "error": f"未另存 xlsx，避免遺漏讀取失敗的工作表：{'、'.join(unread_sheets)}"})
            continue
        if statuses:
            try:
                if progress:
                    progress(f"正在寫入 Excel 比對結果：{path.name}")
                output = write_excel_statuses(path, statuses, legacy_tables=legacy_tables)
                report["excel_outputs"].append({"source": str(path), "output": str(output)})
                if progress:
                    progress(f"已儲存 Excel 比對結果：{output}")
            except Exception as exc:
                report["errors"].append({"file": str(path), "stage": "write_excel",
                                         "error": f"{type(exc).__name__}: {exc}"})
    return ComparisonReport.model_validate(report).to_dict()


def _compare_sheet(table, path: Path, sheet: str, index: dict) -> tuple:
    """工作表結果獨立收集；單列失敗不影響後續列。"""
    header, columns = read_excel_header(table)
    sheet_report = {"file": str(path), "sheet": sheet,
                    "uncompared_columns": [c for c in columns if c and c not in FIELD_MAP and c not in IDENTITY_COLUMNS]}
    rows, errors, statuses = [], [], {}
    for row, location in iter_excel_rows(table, header, columns, path, sheet):
        context = dict(location)
        try:
            context["name"] = _text(row.get("姓名"))
            context.update(_compare_row(row, index))
        except Exception as exc:
            errors.append({"file": str(path), "sheet": sheet, "row": location["row"], "stage": "compare_row",
                           "error": f"{type(exc).__name__}: {exc}"})
            context = {**location,
                       "status": "unmatched", "reason": f"{type(exc).__name__}: {exc}"}
        try:
            context = ComparisonResult.model_validate(context).model_dump(
                mode="json", by_alias=True, exclude_unset=True,
            )
        except ValidationError as exc:
            errors.append({"file": str(path), "sheet": sheet, "row": location["row"], "stage": "validate_result",
                           "error": f"{type(exc).__name__}: {exc}"})
            continue
        rows.append(context)
        statuses[location["row"]] = {"status": context["status"], "reason": context.get("reason")}
    return rows, sheet_report, (header + 1, statuses), errors


def _compare_row(row: dict, index: dict[CaseNumber, list[tuple[Path, JudgmentAnalysis]]]) -> dict:
    try:
        case = CaseNumber(_text(row["裁判機關"]), _non_negative_integer(row["裁判年度"]),
                          _text(row["裁判冠字"]), _non_negative_integer(row["裁判號數"]))
    except (ValueError, TypeError, KeyError) as exc:
        return {"status": "unmatched", "reason": str(exc)}
    base = {"case_number": case.to_string()}
    documents = index.get(case, [])
    if not documents:
        return {**base, "status": "judgment_not_found", "reason": "判決不存在"}
    results = []
    for source, data in documents:
        context = {**base, "json": str(source)}
        try:
            defendants = [d for d in data.defendants if matches_defendant_name(row["姓名"], d.name)]
            if len(defendants) != 1:
                results.append({**context, "status": "unmatched", "reason": "被告姓名無法唯一配對"})
                continue
            defendant = defendants[0]

            # 只比對有罪的 group，若無則視為無法配對。
            guilty_groups = [g for g in defendant.execution_groups if g.executed_jsst in {"科刑", "免刑"}]
            if not guilty_groups:
                results.append({**context, "status": "unmatched", "reason": "找不到有罪的 group"})
                continue
            guilty_crime_ids = {crime_id for g in guilty_groups for crime_id in g.crime_ids}

            # 每列只轉換一次；所有候選均依法條及各罪刑度定位。
            values = {field: _excel_col_to_val(column, row[column])
                      for column, field in FIELD_MAP.items() if column in row}
            for column in ("未遂", "幫助"):
                values.setdefault(FIELD_MAP[column], excel_bool(None, column=column))
            excel_law = LawReference(**{
                field: values[field] for field in LAW_FIELDS if field in values
            })
            candidates = [(crime, law) for crime in defendant.crimes
                          if crime.crime_id in guilty_crime_ids for law in (crime.laws or [])]
            candidates, reason = _filter_candidates(values, candidates, excel_law)
            if not candidates:
                results.append({**context, "status": "unmatched",
                        "reason": reason, "candidate_crime_ids": []})
                continue
            # 多個符合候選也視為定位成功，保留全部候選 ID；累犯由被告資料比較。
            context["candidate_crime_ids"] = list(dict.fromkeys(c.crime_id for c, _ in candidates))
            if "is_recidivist" in values and values["is_recidivist"] != defendant.is_recidivist:
                results.append({**context, "status": "unmatched", "reason": "累犯不合"})
            else:
                results.append({**context, "status": "equal"})
        except (ValueError, TypeError, KeyError) as exc:
            results.append({**context, "status": "unmatched", "reason": str(exc)})
    if len(results) == 1:
        return results[0]
    matched = [result for result in results if result["status"] == "equal"]
    if not matched:
        return {**base, "status": "unmatched",
                "reason": "；".join(dict.fromkeys(r["reason"] for r in results)), "candidate_crime_ids": [],
                "document_results": results}

    # 同案號只要有一份完全符合，就視為配對成功；保留每份判決的結果供查閱。
    return {**base, "status": "equal",
            "candidate_crime_ids": list(dict.fromkeys(cid for result in matched for cid in result["candidate_crime_ids"])),
            "matched_candidates": [{"json": result["json"], "crime_id": cid}
                                   for result in matched for cid in result["candidate_crime_ids"]],
            "document_results": results}


def _filter_candidates(values: dict, candidates: list[tuple[CrimeAnalysis, LawReference]],
                       excel_law: LawReference) -> tuple[list[tuple[CrimeAnalysis, LawReference]], str | None]:
    """依序縮小同一批候選，回報第一個無法通過的條件。"""
    law_fields = LAW_FIELDS - {"is_attempted", "is_accessory"}
    candidates = [(crime, law) for crime, law in candidates
                  if all(getattr(excel_law, field) == getattr(law, field) for field in law_fields)]
    if not candidates:
        return [], "法條不合"
    for field, reason in (("is_attempted", "未遂不合"), ("is_accessory", "幫助犯不合")):
        candidates = [(crime, law) for crime, law in candidates
                      if getattr(excel_law, field) == getattr(law, field)]
        if not candidates:
            return [], reason
    for fields, reason in (({"crime_jsst"}, "裁判情形不合"),
                           (PENALTIES - {"crime_jsst"}, "刑度不合")):
        candidates = [(crime, law) for crime, law in candidates
                      if all(values[field] == getattr(crime, field) for field in fields if field in values)]
        if not candidates:
            return [], reason
    return candidates, None


def _excel_col_to_val(column: str, value):
    """檢核表的分欄法條與刑度解析；基本值交由共用轉換處理。"""
    if column in {"未遂", "幫助", "累犯"}:
        return excel_bool(value, column=column)
    text = excel_optional_text(value)
    if text is None:
        return None
    if column in {"裁判情形", "案由"}:
        return text
    if column in {"條段", "項段"}:
        return text.upper()
    if column == "徒刑(YYMM)":
        return _prison_months(value)
    return excel_integer(value, zero_is_none=column in {"拘役", "罰金", "併科罰金"})


def _prison_months(value) -> int | None:
    number = _non_negative_integer(value)
    years, months = divmod(number, 100)
    if months >= 12:
        raise ValueError(f"YYMM 月份不合法：{value}")
    return years * 12 + months or None


if __name__ == "__main__":
    main()
