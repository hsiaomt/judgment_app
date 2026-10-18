"""Read one judgment, extract structured data with OpenAI, and save as JSON.

Usage: uv run python -m judgment_app.analysis.analyzer judgment.txt --output results.json
"""

import argparse
import json
import os
from pathlib import Path
import re

import requests

from judgment_app.analysis import models
from judgment_app.analysis.models import JudgmentAnalysis
from judgment_app.case_number import CaseNumber
from judgment_app.paths import load_settings, resolve_output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="分析一份判決 TXT／JSON 並輸出 JSON")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, help="輸出 JSON 路徑；預設為來源檔名加 .analysis.json")
    parser.add_argument("--provider", choices=("openai", "gemini"), default="openai")
    parser.add_argument("--model", help="覆寫所選服務的模型設定")
    parser.add_argument("--jid", help="指定 JID；TXT 無 JID 時預設留空，不推測")
    parser.add_argument("--encoding", default="utf-8-sig")
    args = parser.parse_args()
    result = analyze_judgment(
        args.input, args.output, model=args.model, jid=args.jid, provider=args.provider,
        encoding=args.encoding,
    )
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    print(f"已儲存：{_output_path(args.input, args.output)}")


def analyze_source(source: str | Path, output_dir: str | Path, *, provider="openai", progress=None, on_result=None) -> dict:
    """分析單檔或既有批次來源，回傳相同的批次摘要。"""
    source = resolve_output_path(source).resolve()
    output_dir = resolve_output_path(output_dir).resolve()
    if source.is_dir():
        return analyze_folder(source, output_dir=output_dir, provider=provider, progress=progress, on_result=on_result)
    if not source.is_file():
        raise ValueError(f"分析來源不存在：{source}")
    destination = output_dir / (source.name + ".analysis.json")
    report = {"total": 1, "files": [], "errors": [], "outputs": []}
    if progress:
        progress(f"正在分析 1 / 1：{source.name}")
    try:
        analyze_judgment(source, destination, provider=provider)
    except Exception as exc:
        detail = f"分析失敗 [{source}]：{type(exc).__name__}: {exc}"
        report["errors"].append(detail)
        if on_result:
            on_result(detail)
    else:
        report["files"].append(str(source))
        report["outputs"].append(str(destination))
        if on_result:
            on_result(f"分析完成並已儲存：{destination}")
    return report


def analyze_folder(folder: str | Path, *, provider: str = "openai", progress=None, on_result=None,
                   output_dir: str | Path | None = None) -> dict:
    """Use case folders as target cases; other judgments are context only."""
    folder = resolve_output_path(folder).resolve()
    if not folder.is_dir():
        raise ValueError(f"不是有效的資料夾：{folder}")
    output_root = resolve_output_path(output_dir).resolve() if output_dir is not None else None
    report = {"total": 0, "files": [], "errors": [], "outputs": []}
    jobs = []

    def failed(label, exc):
        detail = f"分析失敗 [{label}]：{type(exc).__name__}: {exc}"
        report["errors"].append(detail)
        if on_result:
            on_result(detail)

    for entry in sorted(folder.iterdir()):
        if output_root is not None and entry.resolve() == output_root and output_root != folder:
            continue
        if entry.is_file() and entry.suffix.lower() in {".txt", ".json"} and not entry.name.endswith(".analysis.json"):
            jobs.append((entry, None, []))
        elif entry.is_dir():
            try:
                jobs.extend(_case_folder_jobs(entry))
            except Exception as exc:
                failed(entry.name, exc)
    report["total"] = len(jobs) + len(report["errors"])
    if not jobs:
        return report
    load_settings()
    _provider_settings(provider, None)
    with requests.Session() as session:
        for index, (source, target_case, references) in enumerate(jobs, 1):
            label = str(source.relative_to(folder))
            if progress:
                progress(f"正在分析 {index} / {len(jobs)}：{label}")
            try:
                destination = (output_root / source.relative_to(folder)).with_name(source.name + ".analysis.json") if output_root else _output_path(source, None)
                output_options = {"output_path": destination} if output_root else {}
                analyze_judgment(source, **output_options, session=session,
                                 target_case_number=target_case, reference_documents=references, provider=provider)
            except Exception as exc:
                failed(label, exc)
            else:
                report["files"].append(str(source))
                report["outputs"].append(str(destination))
                if on_result:
                    on_result(f"分析完成並已儲存：{destination}")
    return report


def read_judgment(path: str | Path, *, encoding: str = "utf-8-sig") -> tuple[str, str]:
    """Support TXT, web JSON (jid/text), and judicial API JSON (JID/JFULLX)."""
    path = Path(path)
    if path.suffix.lower() not in {".txt", ".json"}:
        raise ValueError(f"僅支援 TXT 或 JSON：{path}")
    text = path.read_text(encoding=encoding)
    jid = ""
    if path.suffix.lower() == ".json":
        document = json.loads(text)
        if not isinstance(document, dict):
            raise ValueError(f"JSON 必須是一份判決物件：{path}")
        jid = document.get("JID", document.get("jid", ""))
        full = document.get("JFULLX")
        text = full.get("JFULLCONTENT") if isinstance(full, dict) else None
        if text is None:
            text = document.get("text", document.get("JFULL", document.get("JFULLCONTENT")))
    if not isinstance(jid, str):
        raise ValueError(f"JID 必須是字串：{path}")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"找不到非空判決全文：{path}")
    return jid, text


def analyze_judgment(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *, model: str | None = None, jid: str | None = None,
    encoding: str = "utf-8-sig",
    provider: str = "openai", session=None, target_case_number: str | None = None,
    reference_documents: list[dict] | None = None,
) -> JudgmentAnalysis:
    """Replace the output JSON only after receiving a complete, validated response."""
    source = Path(input_path).expanduser().resolve()
    destination = _output_path(source, output_path)
    if source == destination.resolve():
        raise ValueError("JSON 輸出路徑不可與輸入檔相同")
    source_jid, text = read_judgment(source, encoding=encoding)
    expected_jid = source_jid if jid is None else jid
    schema = _schema(JudgmentAnalysis)
    load_settings()
    api_key, selected_model = _provider_settings(provider, model)
    prompt = models.build_prompt(has_target_case=bool(target_case_number))
    input_data = {"jid": expected_jid, "judgment": text}
    if target_case_number:
        input_data["target_case_number"] = target_case_number
        input_data["reference_documents"] = reference_documents or []
    client = session if session is not None else requests
    if provider == "gemini":
        output_text = _gemini_response(client, api_key, selected_model, prompt, input_data, schema)
    else:
        response = client.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": selected_model, "store": False,
                "instructions": prompt,
                "input": json.dumps(input_data, ensure_ascii=False),
                "text": {"format": {"type": "json_schema", "name": "judgment_analysis",
                                     "strict": True, "schema": schema}},
            },
            timeout=(10, 180),
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(
                f"OpenAI HTTP {response.status_code}，request-id="
                f"{response.headers.get('x-request-id', 'unknown')}：{response.text[:1500]}"
            ) from exc
        payload = response.json()
        if payload.get("status") != "completed":
            raise ValueError(f"OpenAI 回應未完成：{payload.get('status')}；{payload.get('incomplete_details')}")
        parts = []
        for item in payload.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "refusal":
                    raise ValueError(f"OpenAI 拒絕分析：{content.get('refusal')}")
                if content.get("type") == "output_text":
                    parts.append(content["text"])
        if not parts:
            raise ValueError("OpenAI 未回傳分析結果")
        output_text = "".join(parts)
    result = _decode(JudgmentAnalysis, json.loads(output_text))
    if target_case_number and result.case_number != target_case_number:
        raise ValueError(f"回應案號不一致：預期 {target_case_number!r}，實際 {result.case_number!r}")
    _validate_values(result)
    _merge_identical_crimes(result)
    save_analysis(destination, result)
    return result


def save_analysis(output_path: str | Path, result: JudgmentAnalysis) -> None:
    """Write validated data as a standalone UTF-8 JSON object."""
    data = result.model_dump()
    _decode(JudgmentAnalysis, data)
    _validate_values(result)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    from tempfile import NamedTemporaryFile
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _output_path(source: str | Path, output: str | Path | None) -> Path:
    return resolve_output_path(output) if output is not None else Path(source).resolve().with_name(
        Path(source).name + ".analysis.json")


def _provider_settings(provider: str, model: str | None) -> tuple[str, str]:
    if provider not in {"openai", "gemini"}:
        raise ValueError(f"不支援的 AI 服務：{provider}")
    key_name = "GEMINI_API_KEY" if provider == "gemini" else "OPENAI_API_KEY"
    key = os.environ.get(key_name, "").strip()
    if not key:
        raise ValueError(f"請在 .env 或環境變數設定 {key_name}")
    default = "gemini-2.5-flash" if provider == "gemini" else "gpt-4o-mini"
    selected = model or os.environ.get(f"{provider.upper()}_MODEL") or default
    if not re.fullmatch(r"[A-Za-z0-9._-]+", selected):
        raise ValueError(f"無效模型名稱：{selected}")
    return key, selected


def _gemini_response(client, api_key, model, prompt, input_data, schema):
    response = client.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": api_key},
        json={
            "systemInstruction": {"parts": [{"text": prompt}]},
            "contents": [{"role": "user", "parts": [{
                "text": json.dumps(input_data, ensure_ascii=False)}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema},
        }, timeout=(10, 180),
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise RuntimeError(f"Gemini HTTP {response.status_code}：{response.text[:1500]}") from exc
    payload = response.json()
    if payload.get("promptFeedback", {}).get("blockReason"):
        raise ValueError(f"Gemini 拒絕分析：{payload['promptFeedback']['blockReason']}")
    candidates = payload.get("candidates", [])
    if not candidates or candidates[0].get("finishReason") != "STOP":
        reason = candidates[0].get("finishReason") if candidates else "沒有候選結果"
        raise ValueError(f"Gemini 回應未完成：{reason}")
    text = "".join(part.get("text", "") for part in candidates[0].get("content", {}).get("parts", [])
                   if not part.get("thought"))
    if not text.strip():
        raise ValueError("Gemini 未回傳分析結果")
    return text


def _merge_identical_crimes(result: JudgmentAnalysis) -> None:
    """Combine identical crimes only within each defendant's execution group."""
    for defendant in result.defendants:
        grouped = {}
        for crime in defendant.crimes:
            fields = crime.model_dump()
            fields.pop("times")
            fields.pop("crime_id")
            fields["groups"] = [i for i, group in enumerate(defendant.execution_groups) if crime.crime_id in group.crime_ids]
            key = json.dumps(fields, ensure_ascii=False, sort_keys=True)
            if key in grouped:
                grouped[key].times += crime.times
                for group in defendant.execution_groups:
                    group.crime_ids = list(dict.fromkeys(grouped[key].crime_id if cid == crime.crime_id else cid for cid in group.crime_ids))
            else:
                grouped[key] = crime
        defendant.crimes = list(grouped.values())


def _validate_values(result: JudgmentAnalysis) -> None:
    """檢查跨欄位的罪名識別碼與分組引用；單欄位限制由模型驗證。"""
    for index, defendant in enumerate(result.defendants):
        location = f"defendants[{index}]"
        ids = {crime.crime_id for crime in defendant.crimes}
        if len(ids) != len(defendant.crimes):
            raise ValueError(f"{location}.crime_id 必須唯一")
        for group_index, group in enumerate(defendant.execution_groups):
            if not set(group.crime_ids) <= ids:
                raise ValueError(f"{location}.execution_groups[{group_index}].crime_ids 引用無效")


def _case_folder_jobs(folder: Path) -> list:
    target = CaseNumber.from_string(folder.name)
    documents = []
    seen = set()
    # Prefer JSON when the same downloaded judgment exists in both formats.
    paths = sorted((p for p in folder.rglob("*")
                    if p.is_file() and p.suffix.lower() in {".txt", ".json"}
                    and not p.name.endswith(".analysis.json")),
                   key=lambda p: (p.suffix.lower() != ".json", str(p)))
    for source in paths:
        jid, text = read_judgment(source)
        case = None
        if jid:
            case = CaseNumber.from_jid(jid)
        else:
            # Only use the document's filename, never case references in its body.
            try:
                case = CaseNumber.from_string(source.stem.split("_", 1)[0])
            except ValueError:
                pass
        key = (source.parent, source.stem)
        if key in seen:
            continue
        seen.add(key)
        documents.append((source, case, {"source": source.name, "jid": jid, "judgment": text}))
    jobs = []
    for source, case, document in documents:
        if case == target:
            references = [item for other, _, item in documents if other != source]
            jobs.append((source, target.to_string(), references))
    if not jobs:
        raise ValueError(f"找不到符合資料夾案號的 TXT／JSON 判決：{folder.name}；請確認檔名或 JSON JID")
    return jobs


def _schema(kind) -> dict:
    """以 Pydantic schema 為基礎，保留回應必須完整輸出所有欄位的規則。"""
    schema = kind.model_json_schema()
    definitions = schema.pop("$defs", {})

    def prepare(node):
        if isinstance(node, list):
            return [prepare(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].removeprefix("#/$defs/")
            node = {**definitions[name], **{k: v for k, v in node.items() if k != "$ref"}}
        result = {key: prepare(value) for key, value in node.items()
                  if key not in {"default", "title"}}
        if result.get("type") == "object":
            result["required"] = list(result.get("properties", {}))
            result["additionalProperties"] = False
        return result

    return prepare(schema)


def _decode(kind, value, location: str = "result"):
    try:
        return kind.model_validate(value, context={"require_all_fields": True})
    except ValueError as exc:
        raise ValueError(f"{location}: {exc}") from exc


if __name__ == "__main__":
    main()
