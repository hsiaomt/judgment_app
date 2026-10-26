"""依執行明細 Excel 建立 JudgmentAnalysis，與分析 JSON 比對完整物件。"""

import argparse
import json
from pathlib import Path
import re

import pandas as pd

from judgment_app.analysis.analysis_models import (
    AnalysisModel, CrimeAnalysis, DefendantAnalysis, ExecutionGroup, JudgmentAnalysis, LawReference,
)
from judgment_app.comparison.comparison_models import ComparisonReport
from judgment_app.comparison.compare_common import (
    IDENTITY_COLUMNS, _is_blank, excel_bool, excel_integer, excel_optional_text, _non_negative_integer,
    find_excel_files, read_excel_header, iter_excel_rows,
    compare_to_report, load_analyses, matches_defendant_name, _text, write_excel_statuses,
)
from judgment_app.case_number import CaseNumber
from judgment_app.paths import resolve_output_path


IGNORED_FIELDS = frozenset({
    "lower_court_case_number", "pros_appearing",
    "appeal_result", "confiscation",
    "crime_ids", "crime_id", "crime_name",

})
FIELD_MAP = {
    "累犯": "is_recidivist", "應執緩刑期間": "probation", "緩刑法條": "probation_law",
    "應執裁判情形": "executed_jsst", "應執徒刑": "executed_penalty",
    "應執徒刑易科標準": "executed_penalty_to_fine", "應執拘役": "executed_detention",
    "應執拘役易科標準": "executed_detention_to_fine", "應執罰金": "executed_fine",
    "應執罰金易勞標準": "executed_fine_to_detention",
    "個案裁判情形": "crime_jsst", "次數": "times", "個案徒刑": "penalty",
    "個案徒刑易科標準": "penalty_to_fine", "個案拘役": "detention",
    "個案拘役易科標準": "detention_to_fine", "個案罰金": "fine",
    "個案罰金易勞標準": "fine_to_detention",
    "檢察官上訴": "is_pros_appeal",
    "未遂": "is_attempted", "幫助": "is_accessory",
}
SCALAR_FIELDS = {
    DefendantAnalysis: set(DefendantAnalysis.model_fields) - {"name", "crimes", "execution_groups"} - IGNORED_FIELDS,
    CrimeAnalysis: set(CrimeAnalysis.model_fields) - {"laws"} - IGNORED_FIELDS,
    ExecutionGroup: set(ExecutionGroup.model_fields) - IGNORED_FIELDS,
}
for _fields in SCALAR_FIELDS.values():
    FIELD_MAP.update({field: field for field in _fields})
FIELD_MAP.update(is_attempted="is_attempted", is_accessory="is_accessory")
REQUIRED_COLUMNS = IDENTITY_COLUMNS | {
    "序號", "個案序號", "個案裁判情形", "應執裁判情形", "次數", "個案案由", "法條",
}
RATE_VALUES = {
    f"{prefix}{number}折算": value
    for prefix in ("易科", "易勞")
    for number, value in (("一仟", 1000), ("二仟", 2000), ("三仟", 3000))
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--excel", default="data/11510.xlsx", help="Excel 檔案或資料夾")
    parser.add_argument("--json-dir", required=True, help="分析 JSON 檔案或資料夾")
    parser.add_argument("--output", default="data/judgment_comparison_report.json")
    args = parser.parse_args()
    report = compare_to_report(
        resolve_output_path(args.excel), resolve_output_path(args.json_dir),
        resolve_output_path(args.output), compare=compare_files,
    )
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"比對報告：{resolve_output_path(args.output)}")
    for output in report["excel_outputs"]:
        print(f"Excel 比對結果：{output['output']}")


def compare_files(excel_path: Path, json_path: Path, *, progress=None, excel_files: list[Path] | None = None) -> dict:
    """依案號建立完整物件，再依 Excel 序號分別比對被告；各被告獨立產生結果並回寫來源列。"""
    report = {"rows": [], "errors": [], "sheets": [], "excel_outputs": [],
              "ignored_fields": sorted(IGNORED_FIELDS)}
    index = load_analyses(Path(json_path), report["errors"])
    workbooks = {}
    cases = _read_excel_cases(Path(excel_path), report, progress, workbooks=workbooks, excel_files=excel_files)
    for case, records in cases.items():
        defendants = {}
        for row, location in records:
            defendants.setdefault(_required_id(row, "序號"), []).append((row, location))
        build_errors = {}
        expected = build_judgment_analysis(
            case.to_string(), [row for row, _ in records], errors=build_errors,
        )
        comparisons = [(source, compare_analyses(expected, actual))
                       for source, actual in index.get(case, [])]
        valid_positions = iter(range(len(expected.defendants)))
        for sequence, defendant_records in defendants.items():
            name = _text(defendant_records[0][0].get("姓名"))
            context = {"case_number": expected.case_number, "name": name,
                       "excel_rows": [location for _, location in defendant_records]}
            if sequence in build_errors:
                reason = build_errors[sequence]
                report["errors"].append({**context, "stage": "build_judgment", "error": reason})
                report["rows"].append({**context, "status": "unmatched", "reason": reason})
                continue
            position = next(valid_positions)
            if not comparisons:
                report["rows"].append({**context, "status": "judgment_not_found", "reason": "判決不存在"})
                continue
            documents = [{"json": str(source), **result["defendant_results"][position]}
                         for source, result in comparisons]
            if any(document["status"] == "equal" for document in documents):
                result = {"status": "equal", "document_results": documents}
            else:
                result = {"status": "unmatched", "document_results": documents,
                          "reason": "；".join(dict.fromkeys(d["reason"] for d in documents))}
            report["rows"].append({**context, **result})
    _write_comparison_results(report, workbooks, progress)
    return ComparisonReport.model_validate(report).to_dict()


def compare_analyses(expected: JudgmentAnalysis, actual: JudgmentAnalysis) -> dict:
    """統一物件比對入口；回傳整案結果及依 expected.defendants 順序排列的 defendant_results。"""
    left = CaseNumber.from_string(expected.case_number).to_string()
    right = CaseNumber.from_string(actual.case_number).to_string()
    if left != right:
        result = {"status": "unmatched", "reason": "case_number 不合",
                  "differences": _differences(left, right, "case_number")}
        return {**result, "defendant_results": [dict(result) for _ in expected.defendants]}
    defendant_results = [_compare_defendant(defendant, actual.defendants, index)
                         for index, defendant in enumerate(expected.defendants)]
    differences = []
    for result in defendant_results:
        if result["status"] != "equal" and "differences" not in result:
            return {**result, "defendant_results": defendant_results}
        differences.extend(result["differences"])
    return {**_comparison_result(differences), "defendant_results": defendant_results}


def build_judgment_analysis(case_number: str, rows: list[dict], *,
                            errors: dict[int, str] | None = None) -> JudgmentAnalysis:
    """同案號一個判決、同序號一個被告；提供 errors 時隔離並記錄無效被告。"""
    case = CaseNumber.from_string(case_number)
    grouped_rows = {}
    for row in rows:
        grouped_rows.setdefault(_required_id(row, "序號"), []).append(row)
    defendants = []
    for sequence, defendant_rows in grouped_rows.items():
        try:
            defendants.append(_build_defendant(case, defendant_rows))
        except (ValueError, TypeError, KeyError) as exc:
            if errors is None:
                raise
            errors[sequence] = f"{type(exc).__name__}: {exc}"
    return JudgmentAnalysis(case_number=case.to_string(), defendants=defendants)


def excel_row_to_law_reference(row: dict) -> LawReference | None:
    """法條九碼：條(3)＋之(1)＋條段(1)＋項(1)＋項段(1)＋款(2)。"""
    code = _text(row.get("法條"))
    if not code or code.lower() == "x" or code == "000000000" or code == "0":
        return None
    match = re.fullmatch(r"([0-9]{3})([0-9])([xX0ABCabc])([0-9])([xX0ABCabc])([0-9]{2})", code)
    if match is None:
        raise ValueError(f"法條九碼格式無法辨識：{code}")
    if not _text(row.get("個案案由")):
        raise ValueError("有法條時個案案由不可空白")
    article, sub, part, paragraph, paragraph_part, clause = match.groups()
    return LawReference(
        law_name=_text(row.get("個案案由")), article=int(article), article_sub=int(sub) or None,
        article_part=part.upper() if part.upper() in {"A", "B", "C"} else None,
        paragraph=int(paragraph) or None,
        paragraph_part=paragraph_part.upper() if paragraph_part.upper() in {"A", "B", "C"} else None,
        paragraph_sub=int(clause) or None,
        is_attempted=excel_bool(row.get("未遂", row.get("is_attempted")), column="未遂"),
        is_accessory=excel_bool(row.get("幫助", row.get("is_accessory")), column="幫助"),
    )


def _write_comparison_results(report: dict, workbooks: dict, progress) -> None:
    """將各被告結果寫回其來源列；格式錯誤列則寫入該列自己的結果。"""
    for result in report["rows"]:
        for location in result.get("excel_rows", [result]):
            state = workbooks[Path(location["excel"])]
            statuses = state["statuses"][location["sheet"]][1]
            statuses[location["row"]] = {"status": result["status"], "reason": result.get("reason")}
    for source, state in workbooks.items():
        if state["legacy_tables"] is not None and state["unread_sheets"]:
            report["errors"].append({"file": str(source), "stage": "write_excel",
                "error": f"未另存 xlsx，避免遺漏讀取失敗的工作表：{'、'.join(state['unread_sheets'])}"})
            continue
        if not state["statuses"]:
            continue
        try:
            if progress:
                progress(f"正在寫入 Excel 比對結果：{source.name}")
            output = write_excel_statuses(source, state["statuses"], legacy_tables=state["legacy_tables"])
            report["excel_outputs"].append({"source": str(source), "output": str(output)})
        except Exception as exc:
            report["errors"].append({"file": str(source), "stage": "write_excel",
                                     "error": f"{type(exc).__name__}: {exc}"})


def _read_excel_cases(path: Path, report: dict, progress, *, workbooks: dict | None = None,
                      excel_files: list[Path] | None = None) -> dict:
    files = find_excel_files(path) if excel_files is None else excel_files
    cases = {}
    for source in files:
        state = {"statuses": {}, "legacy_tables": {} if source.suffix.lower() == ".xls" else None,
                 "unread_sheets": []}
        if workbooks is not None:
            workbooks[source] = state
        if progress:
            progress(f"正在彙整 Excel：{source.name}")
        try:
            with pd.ExcelFile(source) as book:
                for sheet in book.sheet_names:
                    table = None
                    try:
                        table = book.parse(sheet, header=None, dtype=object)
                        if state["legacy_tables"] is not None:
                            state["legacy_tables"][sheet] = table
                        header, columns = read_excel_header(table)
                        missing = REQUIRED_COLUMNS - columns.keys()
                        if missing:
                            raise ValueError(f"缺少執行明細欄位：{'、'.join(sorted(missing))}")
                        state["statuses"][sheet] = (header + 1, {})
                        supplied = {FIELD_MAP[column] for column in columns if column in FIELD_MAP}
                        supplied.add("is_pros_appeal")  # 上訴類別與審級共同決定。
                        defaulted = set().union(*SCALAR_FIELDS.values(), {"is_attempted", "is_accessory"}) - supplied
                        report["sheets"].append({
                            "file": str(source), "sheet": sheet,
                            "uncompared_columns": [column for column in columns if column not in
                                set(FIELD_MAP) | REQUIRED_COLUMNS | {"上訴類別"}],
                            "defaulted_fields": sorted(defaulted),
                        })
                        for row, location in iter_excel_rows(table, header, columns, source, sheet):
                            try:
                                _required_id(row, "序號")
                                case = CaseNumber(_text(row["裁判機關"]), _non_negative_integer(row["裁判年度"]),
                                                  _text(row["裁判冠字"]), _non_negative_integer(row["裁判號數"]))
                            except (ValueError, TypeError, KeyError) as exc:
                                reason = f"{type(exc).__name__}: {exc}"
                                report["errors"].append({**location, "error": reason})
                                report["rows"].append({**location, "status": "unmatched", "reason": reason})
                                continue
                            cases.setdefault(case, []).append((row, location))
                    except Exception as exc:
                        if table is None:
                            state["unread_sheets"].append(sheet)
                        report["errors"].append({"file": str(source), "sheet": sheet,
                                                 "error": f"{type(exc).__name__}: {exc}"})
        except Exception as exc:
            state["unread_sheets"].append("工作簿")
            report["errors"].append({"file": str(source), "error": f"{type(exc).__name__}: {exc}"})
    return cases


def _compare_defendant(expected: DefendantAnalysis, actual: list[DefendantAnalysis],
                       index: int = 0) -> dict:
    """姓名符合的候選中，只要有一位完整相符即通過，不跨被告拼接欄位。"""
    candidates = [other for other in actual if matches_defendant_name(expected.name, other.name)]
    if not candidates:
        return {"status": "unmatched", "reason": f"找不到姓名相符的被告：{expected.name}"}
    path = f"defendants[{index}]"
    results = []
    for other in candidates:
        differences = [difference for field in sorted(SCALAR_FIELDS[DefendantAnalysis])
                       for difference in _differences(getattr(expected, field), getattr(other, field),
                                                      f"{path}.{field}")]
        differences.extend(_crime_differences(expected.crimes, other.crimes, f"{path}.crimes[]"))
        differences.extend(_execution_group_differences(
            expected.execution_groups, other.execution_groups, f"{path}.execution_groups",
        ))
        if not differences:
            return _comparison_result([])
        results.append(differences)
    return _comparison_result(min(results, key=len))


def _comparison_result(differences: list[dict]) -> dict:
    if differences:
        fields = dict.fromkeys(field for difference in differences for field in _difference_fields(
            difference["excel"], difference["json"], difference["field"],
        ))
        reason = "；".join(f"{field} 不合" for field in fields)
        return {"status": "unmatched", "reason": reason, "differences": differences}
    return {"status": "equal", "differences": []}


def _difference_fields(expected, actual, path: str) -> list[str]:
    """清單以欄位值的集合（保留次數）說明差異，不猜測不同罪的配對關係。"""
    if _equal_values(expected, actual):
        return []
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"{path}.length（數量）"]
        items = expected + actual
        if items and all(isinstance(item, dict) for item in items):
            keys = sorted(set().union(*(item.keys() for item in items)) - IGNORED_FIELDS)
            fields = [field for key in keys for field in _difference_fields(
                [item.get(key) for item in expected], [item.get(key) for item in actual], f"{path}[].{key}",
            )]
            return fields or [f"{path}（欄位配對關係）"]
        if items and all(isinstance(item, list) for item in items):
            fields = _difference_fields(
                [value for item in expected for value in item],
                [value for item in actual for value in item], path,
            )
            return fields or [f"{path}（分組關係）"]
    return [path]


def _execution_group_differences(expected: list[ExecutionGroup], actual: list[ExecutionGroup], path: str) -> list[dict]:
    """各 Excel 組尋找符合應執裁判情形的 group；不限制組數或一對一配對。"""
    differences = []
    for index, group in enumerate(expected):
        candidates = [candidate for candidate in actual
                      if candidate.executed_jsst == group.executed_jsst]
        if not candidates:
            differences.append({"field": f"{path}[{index}].executed_jsst",
                                "excel": group.executed_jsst,
                                "json": sorted({candidate.executed_jsst for candidate in actual})})
            continue
        # 無罪、免訴、不受理、免刑只需存在對應裁判情形的 group。
        if group.executed_jsst != "科刑":
            continue
        results = [_differences(group, candidate, f"{path}[{index}]") for candidate in candidates]
        if all(results):
            differences.extend(min(results, key=len))
    return differences


def _crime_differences(expected: list[CrimeAnalysis], actual: list[CrimeAnalysis], path: str) -> list[dict]:
    """每個 Excel 罪須有完整相符的 JSON 罪；不比較罪清單長度，可重用候選。"""
    differences = []
    for crime in expected:
        if not actual:
            differences.append({"field": path, "excel": _report_value(crime), "json": None})
            continue
        results = [_differences(crime, candidate, path) for candidate in actual]
        if all(results):
            differences.extend(min(results, key=len))
    return differences


def _differences(expected, actual, path: str = "") -> list[dict]:
    if _equal_values(expected, actual):
        return []
    if isinstance(expected, AnalysisModel) and type(expected) is type(actual):
        return [difference for field in sorted(type(expected).model_fields) if field not in IGNORED_FIELDS
                for difference in _differences(getattr(expected, field), getattr(actual, field),
                                                f"{path}.{field}" if path else field)]
    return [{"field": path, "excel": _report_value(expected), "json": _report_value(actual)}]


def _equal_values(expected, actual) -> bool:
    """直接比較模型屬性；清單忽略順序但保留重複次數。"""
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, AnalysisModel):
        return all(_equal_values(getattr(expected, field), getattr(actual, field))
                   for field in type(expected).model_fields if field not in IGNORED_FIELDS)
    if isinstance(expected, dict):
        return all(_equal_values(expected.get(key), actual.get(key))
                   for key in expected.keys() | actual.keys() if key not in IGNORED_FIELDS)
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return False
        remaining = list(actual)
        for item in expected:
            for index, candidate in enumerate(remaining):
                if _equal_values(item, candidate):
                    remaining.pop(index)
                    break
            else:
                return False
        return True
    return expected == actual


def _report_value(value):
    """僅在產生差異報告時將模型轉成可輸出的資料。"""
    if isinstance(value, AnalysisModel):
        return {field: _report_value(getattr(value, field))
                for field in type(value).model_fields if field not in IGNORED_FIELDS}
    if isinstance(value, list):
        return [_report_value(item) for item in value]
    return value


def _build_defendant(case: CaseNumber, rows: list[dict]) -> DefendantAnalysis:
    """同個案序號合併法條；依應執裁判情形及三種折算標準分組。"""
    state = {"values": {}, "crimes": {}, "groups": {}}
    name = _text(rows[0].get("姓名"))
    for row in rows:
        try:
            crime_sequence = _required_id(row, "個案序號")
            if not name:
                raise ValueError("被告姓名不可空白")
            if _text(row.get("姓名")) != name:
                raise ValueError("同一序號的被告姓名不一致")
            values = _row_values(row, case)
            _merge_values(state["values"], _select(values, DefendantAnalysis))
            crime = CrimeAnalysis(
                crime_id=1, crime_name="Excel", laws=None, **_select(values, CrimeAnalysis),
            )
            group = ExecutionGroup(crime_ids=[1], **_select(values, ExecutionGroup))
            group_key = (group.executed_jsst, group.executed_penalty_to_fine,
                         group.executed_detention_to_fine, group.executed_fine_to_detention)
            law = excel_row_to_law_reference(row)
            if crime_sequence in state["crimes"]:
                existing = state["crimes"][crime_sequence]
                if any(getattr(existing, field) != getattr(crime, field)
                       for field in SCALAR_FIELDS[CrimeAnalysis]):
                    raise ValueError("同一罪的裁判情形、次數或刑度等欄位不一致")
                if (existing.laws is None) != (law is None):
                    raise ValueError("同一罪的法條同時有空值與非空值")
                if law is not None and law not in existing.laws:
                    existing.laws.append(law)
            else:
                crime.crime_id = len(state["crimes"]) + 1
                crime.laws = [law] if law is not None else None
                state["crimes"][crime_sequence] = crime
            crime_id = state["crimes"][crime_sequence].crime_id
            if group_key in state["groups"]:
                existing_group = state["groups"][group_key]
                if any(getattr(existing_group, field) != getattr(group, field)
                       for field in SCALAR_FIELDS[ExecutionGroup]):
                    raise ValueError("同一應執行刑分組的刑度欄位不一致")
                if crime_id not in existing_group.crime_ids:
                    existing_group.crime_ids.append(crime_id)
            else:
                group.crime_ids = [crime_id]
                state["groups"][group_key] = group
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"序號={_text(row.get('序號'))}、個案序號={_text(row.get('個案序號'))}：{exc}") from exc
    return DefendantAnalysis(name=name, crimes=list(state["crimes"].values()),
                             execution_groups=list(state["groups"].values()), **state["values"])


def _merge_values(target: dict, values: dict) -> None:
    for field, value in values.items():
        if field in target and target[field] != value:
            raise ValueError(f"同一判決／被告的 {field} 在不同列不一致")
        target[field] = value


def _required_id(row: dict, column: str) -> int:
    if _is_blank(row.get(column)):
        raise ValueError(f"{column}不可空白")
    return _non_negative_integer(row[column])


def _select(values: dict, model) -> dict:
    return {field: values[field] for field in SCALAR_FIELDS[model] if field in values}


def _row_values(row: dict, case: CaseNumber) -> dict:
    values = {"is_recidivist": False}
    supplied = set()
    for column, raw in row.items():
        field = FIELD_MAP.get(column)
        if field is None:
            continue
        text = excel_optional_text(raw)
        if field in {"is_recidivist", "is_attempted", "is_accessory", "is_pros_appeal"}:
            value = excel_bool(raw, column=column, blank=None if field == "is_pros_appeal" else False)
        elif text is None:
            value = None
        elif field in {"crime_jsst", "executed_jsst"}:
            value = text
        elif column in {"個案徒刑", "應執徒刑"}:
            value = _prison_months(raw)
        elif column == "應執緩刑期間":
            number = _non_negative_integer(raw)
            years, months = divmod(number, 100)
            if months:
                raise ValueError(f"緩刑期間無法以整年表示：{raw}")
            value = years or None
        elif field.endswith(("_to_fine", "_to_detention")):
            value = RATE_VALUES[text] if text in RATE_VALUES else excel_integer(raw, zero_is_none=True)
        else:
            value = excel_integer(raw, zero_is_none=field != "times")
        if field in supplied and values[field] != value:
            raise ValueError(f"同一欄位的標題別名內容不一致：{field}")
        values[field] = value
        supplied.add(field)
    if "is_pros_appeal" not in supplied:
        is_first_instance = "地方法院" in case.court_name and "上" not in case.case
        values["is_pros_appeal"] = True if not _is_blank(row.get("上訴類別")) else (
            None if is_first_instance else False
        )
    return values


def _prison_months(value) -> int | None:
    number = _non_negative_integer(value)
    years, remainder = divmod(number, 10000)
    months, days = divmod(remainder, 100)
    if months >= 12 or days:
        raise ValueError(f"徒刑年月日無法轉為整月：{value}")
    return years * 12 + months or None


if __name__ == "__main__":
    main()
