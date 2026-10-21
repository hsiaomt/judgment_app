"""依執行明細 Excel 建立 JudgmentAnalysis，與分析 JSON 比對完整物件。"""

import argparse
from collections import Counter
import json
from pathlib import Path
import re

import pandas as pd

from judgment_app.analysis.analysis_models import (
    CrimeAnalysis, DefendantAnalysis, ExecutionGroup, JudgmentAnalysis, LawReference,
)
from judgment_app.analysis.compare import (
    IDENTITY_COLUMNS, _blank, _excel_col_to_val, _files, _integer,
    _matches_defendant_name, _text,
)
from judgment_app.case_number import CaseNumber
from judgment_app.paths import resolve_output_path


# lower_case_number 是需求中的名稱；模型實際名稱為 lower_court_case_number。
IGNORED_FIELDS = frozenset({
    "lower_case_number", "lower_court_case_number", "confiscation",
    "crime_ids", "crime_id", "crime_name",
    "pros_appearing", "appeal_result", "addition_fine", "addition_fine_to_detention",
    "executed_addition_fine", "executed_addition_fine_to_detention",
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
    f"{prefix}{number}仟折算": value
    for prefix in ("易科", "易勞")
    for number, value in (("一", 1000), ("二", 2000), ("三", 3000))
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--excel", default="data/執行明細.xlsx", help="Excel 檔案或資料夾")
    parser.add_argument("--json-dir", required=True, help="分析 JSON 檔案或資料夾")
    parser.add_argument("--output", default="data/judgment_comparison_report.json")
    args = parser.parse_args()
    report = compare_to_report(
        resolve_output_path(args.excel), resolve_output_path(args.json_dir),
        resolve_output_path(args.output),
    )
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"比對報告：{resolve_output_path(args.output)}")


def compare_to_report(excel_path: Path, json_path: Path, destination: Path, *, progress=None) -> dict:
    """另存報告，不回寫來源 Excel 或分析 JSON。"""
    destination = resolve_output_path(destination).resolve()
    if destination.suffix.lower() != ".json":
        raise ValueError("比對報告必須為 .json 檔案")
    if any(Path(source).resolve() == destination for source in (excel_path, json_path)):
        raise ValueError("比對報告不可覆寫來源檔案")
    if destination.exists():
        data = json.loads(destination.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and "case_number" in data:
            raise ValueError("比對報告不可覆寫分析 JSON")
    report = compare_files(excel_path, json_path, progress=progress)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def compare_files(excel_path: Path, json_path: Path, *, progress=None) -> dict:
    """依案號彙整 Excel，每案與各份同案號 JSON 獨立比對。"""
    report = {"rows": [], "errors": [], "sheets": [], "ignored_fields": sorted(IGNORED_FIELDS)}
    index = _load_analyses(Path(json_path), report["errors"])
    cases = _read_excel_cases(Path(excel_path), report, progress)
    for case, records in cases.items():
        context = {"case_number": case.to_string(), "excel_rows": [location for _, location in records]}
        try:
            expected = build_judgment_analysis(case.to_string(), [row for row, _ in records])
        except (ValueError, TypeError, KeyError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            report["errors"].append({**context, "stage": "build_judgment", "error": reason})
            report["rows"].append({**context, "status": "unmatched", "reason": reason})
            continue
        documents = [{"json": str(source), **compare_analyses(expected, actual)}
                     for source, actual in index.get(case, [])]
        if not documents:
            result = {"status": "unmatched", "reason": "判決不存在"}
        elif any(document["status"] == "equal" for document in documents):
            result = {"status": "equal", "document_results": documents}
        else:
            result = {"status": "unmatched", "document_results": documents,
                      "reason": "；".join(dict.fromkeys(d["reason"] for d in documents))}
        report["rows"].append({**context, **result})
    report["summary"] = dict(Counter(row["status"] for row in report["rows"]))
    report["summary"].update(total=len(report["rows"]), errors=len(report["errors"]))
    return report


def build_judgment_analysis(case_number: str, rows: list[dict]) -> JudgmentAnalysis:
    """同一被告內以 (序號, 個案序號) 建罪、以序號建組，不依刑度猜測分組。"""
    case = CaseNumber.from_string(case_number)
    defendants = {}
    for row in rows:
        try:
            sequence = _required_id(row, "序號")
            crime_sequence = _required_id(row, "個案序號")
            name = _text(row.get("姓名"))
            if not name:
                raise ValueError("被告姓名不可空白")
            values = _row_values(row, case)
            state = defendants.setdefault(name, {"values": {}, "crimes": {}, "groups": {}})
            _merge_values(state["values"], _select(values, DefendantAnalysis))
            crime = CrimeAnalysis(
                crime_id=1, crime_name="Excel", laws=None, **_select(values, CrimeAnalysis),
            )
            group = ExecutionGroup(crime_ids=[1], **_select(values, ExecutionGroup))
            law = excel_row_to_law_reference(row)
            key = (sequence, crime_sequence)
            if key in state["crimes"]:
                existing = state["crimes"][key]
                if _canonical(existing.model_dump(exclude={"laws"})) != _canonical(crime.model_dump(exclude={"laws"})):
                    raise ValueError("同一罪的裁判情形、次數或刑度等欄位不一致")
                if (existing.laws is None) != (law is None):
                    raise ValueError("同一罪的法條同時有空值與非空值")
                if law is not None and law not in existing.laws:
                    existing.laws.append(law)
            else:
                crime.crime_id = len(state["crimes"]) + 1
                crime.laws = [law] if law is not None else None
                state["crimes"][key] = crime
            crime_id = state["crimes"][key].crime_id
            if sequence in state["groups"]:
                existing_group = state["groups"][sequence]
                if _canonical(existing_group.model_dump()) != _canonical(group.model_dump()):
                    raise ValueError("同一序號的應執行刑欄位不一致")
                if crime_id not in existing_group.crime_ids:
                    existing_group.crime_ids.append(crime_id)
            else:
                group.crime_ids = [crime_id]
                state["groups"][sequence] = group
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"序號={_text(row.get('序號'))}、個案序號={_text(row.get('個案序號'))}：{exc}") from exc
    return JudgmentAnalysis(
        case_number=case.to_string(),
        defendants=[DefendantAnalysis(name=name, crimes=list(state["crimes"].values()),
                                      execution_groups=list(state["groups"].values()), **state["values"])
                    for name, state in defendants.items()],
    )


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
        is_attempted=_excel_col_to_val("未遂", row.get("未遂", row.get("is_attempted"))),
        is_accessory=_excel_col_to_val("幫助", row.get("幫助", row.get("is_accessory"))),
    )


def compare_analyses(expected: JudgmentAnalysis, actual: JudgmentAnalysis) -> dict:
    """清單不計順序但保留數量；被告沿用原 compare 的遮蔽姓名比對。"""
    left, right = expected.model_dump(), actual.model_dump()
    left["case_number"] = CaseNumber.from_string(expected.case_number).to_string()
    right["case_number"] = CaseNumber.from_string(actual.case_number).to_string()
    matched, ordered = set(), []
    for defendant in left["defendants"]:
        candidates = [i for i, other in enumerate(actual.defendants)
                      if _matches_defendant_name(defendant["name"], other.name)]
        if len(candidates) != 1 or candidates[0] in matched:
            return {"status": "unmatched", "reason": f"被告姓名無法唯一配對：{defendant['name']}"}
        position = candidates[0]
        matched.add(position)
        other = right["defendants"][position]
        other["name"] = defendant["name"]
        ordered.append(other)
    ordered.extend(other for i, other in enumerate(right["defendants"]) if i not in matched)
    right["defendants"] = ordered
    differences = _differences(left, right)
    if differences:
        return {"status": "unmatched", "reason": "JudgmentAnalysis 內容不合", "differences": differences}
    return {"status": "equal", "differences": []}


def _read_excel_cases(path: Path, report: dict, progress) -> dict:
    files = _files(path, {".xls", ".xlsx"})
    if path.is_dir():
        files = [file for file in files if not re.search(r"\.status(?:-\d+)?\.xlsx$", file.name, re.IGNORECASE)]
    if not files:
        raise ValueError(f"找不到 Excel：{path}")
    cases = {}
    for source in files:
        if progress:
            progress(f"正在彙整 Excel：{source.name}")
        try:
            with pd.ExcelFile(source) as book:
                for sheet in book.sheet_names:
                    try:
                        table = book.parse(sheet, header=None, dtype=object)
                        header = next((i for i, row in table.iterrows()
                                       if IDENTITY_COLUMNS <= {_text(x) for x in row}), None)
                        if header is None:
                            raise ValueError("找不到裁判案號及姓名標題列")
                        columns = {}
                        for i, name in enumerate(table.iloc[header]):
                            columns.setdefault(_text(name), i)
                        columns.pop("", None)
                        columns.pop("status", None)
                        missing = REQUIRED_COLUMNS - columns.keys()
                        if missing:
                            raise ValueError(f"缺少執行明細欄位：{'、'.join(sorted(missing))}")
                        supplied = {FIELD_MAP[column] for column in columns if column in FIELD_MAP}
                        supplied.add("is_pros_appeal")  # 上訴類別與審級共同決定。
                        defaulted = set().union(*SCALAR_FIELDS.values(), {"is_attempted", "is_accessory"}) - supplied
                        report["sheets"].append({
                            "file": str(source), "sheet": sheet,
                            "uncompared_columns": [column for column in columns if column not in
                                set(FIELD_MAP) | REQUIRED_COLUMNS | {"上訴類別"}],
                            "defaulted_fields": sorted(defaulted),
                        })
                        for i in range(header + 1, len(table)):
                            row = {name: table.iat[i, col] for name, col in columns.items()}
                            if all(_blank(value) for value in row.values()):
                                continue
                            location = {"excel": str(source), "sheet": sheet, "row": i + 1}
                            try:
                                case = CaseNumber(_text(row["裁判機關"]), _integer(row["裁判年度"]),
                                                  _text(row["裁判冠字"]), _integer(row["裁判號數"]))
                            except (ValueError, TypeError, KeyError) as exc:
                                reason = f"{type(exc).__name__}: {exc}"
                                report["errors"].append({**location, "error": reason})
                                report["rows"].append({**location, "status": "unmatched", "reason": reason})
                                continue
                            cases.setdefault(case, []).append((row, location))
                    except Exception as exc:
                        report["errors"].append({"file": str(source), "sheet": sheet,
                                                 "error": f"{type(exc).__name__}: {exc}"})
        except Exception as exc:
            report["errors"].append({"file": str(source), "error": f"{type(exc).__name__}: {exc}"})
    return cases


def _load_analyses(path: Path, errors: list) -> dict:
    """先移除不參與比較的選填欄位，兼容仍含舊併科罰金欄位的 JSON。"""
    index = {}
    for source in _files(path, {".json"}):
        try:
            data = json.loads(source.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or "case_number" not in data:
                continue
            analysis = JudgmentAnalysis.from_dict(_without_ignored_optional(data))
            case = CaseNumber.from_string(analysis.case_number)
            index.setdefault(case, []).append((source, analysis))
        except (ValueError, TypeError, KeyError, OSError) as exc:
            errors.append({"file": str(source), "error": f"{type(exc).__name__}: {exc}"})
    return index


def _without_ignored_optional(value):
    if isinstance(value, dict):
        return {key: _without_ignored_optional(item) for key, item in value.items()
                if key not in IGNORED_FIELDS or key in {"crime_id", "crime_ids", "crime_name"}}
    if isinstance(value, list):
        return [_without_ignored_optional(item) for item in value]
    return value


def _row_values(row: dict, case: CaseNumber) -> dict:
    values = {"is_recidivist": False}
    supplied = set()
    for column, raw in row.items():
        field = FIELD_MAP.get(column)
        if field is None:
            continue
        text = _text(raw)
        if field in {"is_recidivist", "is_attempted", "is_accessory", "is_pros_appeal"}:
            value = None if field == "is_pros_appeal" and not text else _excel_col_to_val("累犯", raw)
        elif not text or text.lower() == "x":
            value = None
        elif field in {"crime_jsst", "executed_jsst"}:
            value = text
        elif column in {"個案徒刑", "應執徒刑"}:
            value = _prison_months(raw)
        elif column == "應執緩刑期間":
            number = _integer(raw)
            years, months = divmod(number, 100)
            if months:
                raise ValueError(f"緩刑期間無法以整年表示：{raw}")
            value = years or None
        elif field.endswith(("_to_fine", "_to_detention")):
            value = RATE_VALUES[text] if text in RATE_VALUES else _integer(raw) or None
        else:
            value = _integer(raw)
            if field != "times":
                value = value or None
        if field in supplied and values[field] != value:
            raise ValueError(f"同一欄位的標題別名內容不一致：{field}")
        values[field] = value
        supplied.add(field)
    if "is_pros_appeal" not in supplied:
        is_first_instance = "地方法院" in case.court_name and "上" not in case.case
        values["is_pros_appeal"] = True if not _blank(row.get("上訴類別")) else (
            None if is_first_instance else False
        )
    return values


def _prison_months(value) -> int | None:
    number = _integer(value)
    years, remainder = divmod(number, 10000)
    months, days = divmod(remainder, 100)
    if months >= 12 or days:
        raise ValueError(f"徒刑年月日無法轉為整月：{value}")
    return years * 12 + months or None


def _required_id(row: dict, column: str) -> int:
    if _blank(row.get(column)):
        raise ValueError(f"{column}不可空白")
    return _integer(row[column])


def _select(values: dict, model) -> dict:
    return {field: values[field] for field in SCALAR_FIELDS[model] if field in values}


def _merge_values(target: dict, values: dict) -> None:
    for field, value in values.items():
        if field in target and _canonical(target[field]) != _canonical(value):
            raise ValueError(f"同一判決／被告的 {field} 在不同列不一致")
        target[field] = value


def _normalized(value):
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in value.items() if key not in IGNORED_FIELDS}
    if isinstance(value, list):
        return sorted((_normalized(item) for item in value), key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return value


def _canonical(value) -> str:
    return json.dumps(_normalized(value), ensure_ascii=False, sort_keys=True)


def _differences(expected, actual, path: str = "") -> list[dict]:
    if _canonical(expected) == _canonical(actual):
        return []
    if isinstance(expected, dict) and isinstance(actual, dict):
        return [difference for key in sorted(expected.keys() | actual.keys()) if key not in IGNORED_FIELDS
                for difference in _differences(expected.get(key), actual.get(key), f"{path}.{key}" if path else key)]
    if path == "defendants" and len(expected) == len(actual):
        return [difference for i, (left, right) in enumerate(zip(expected, actual))
                for difference in _differences(left, right, f"{path}[{i}]")]
    return [{"field": path, "excel": _normalized(expected), "json": _normalized(actual)}]


if __name__ == "__main__":
    main()
