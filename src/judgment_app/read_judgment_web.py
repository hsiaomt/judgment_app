"""透過裁判書網站讀取全文；也可執行 Excel 循序批次測試。"""

import argparse
import json
import time
import re
import unicodedata
from urllib.parse import urljoin
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Comment, NavigableString

from judgment_app.find_jid import search_judgments
from judgment_app.exceptions import ApiResponseError
from judgment_app.read_case_numbers import COURT_MAP, read_case_numbers
from judgment_app.paths import resolve_output_path

DOCUMENT_URL = "https://judgment.judicial.gov.tw/FJUD/data.aspx"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("excel", type=Path)
    parser.add_argument("--output", type=Path, default=resolve_output_path(Path("data") / datetime.now().strftime("web_test_%Y%m%d_%H%M%S")))
    parser.add_argument("--interval", type=float, default=0, help="案號之間等待秒數，預設連續循序請求")
    args = parser.parse_args()
    if args.interval < 0:
        parser.error("interval 不可小於零")
    report = run_batch(args.excel, args.output, args.interval)
    print(f"完成 {report['completed_cases']}/{report['case_count']} 案號；{report['documents']} 份全文；{report['elapsed_seconds']} 秒")
    print(f"紀錄：{args.output / 'report.json'}")
    if report["error"]:
        raise SystemExit(1)


def run_batch(excel: Path, output: Path, interval: float = 0):
    cases = read_case_numbers(excel, "AL", "AM", "AN", "AO", start_row=4)
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "started_at": datetime.now().astimezone().isoformat(),
        "case_count": len(cases), "interval": interval,
        "cases": [], "requests": [], "documents": 0, "error": None,
    }
    started = time.perf_counter()
    seen = set()
    try:
        with MeasuredSession(report["requests"]) as session:
            for index, case in enumerate(cases, 1):
                entry = {"case": case, "jids": [], "completed": False}
                report["cases"].append(entry)
                print(f"[{index}/{len(cases)}] {case}", flush=True)
                jids = search_judgments(**case, session=session)
                entry["jids"] = jids
                for jid in jids:
                    if jid in seen:
                        continue
                    document = get_judgment_web(jid, session=session)
                    # 使用序號作檔名，JID 留在 JSON，避免不合法的路徑字元。
                    path = output / f"judgment_{len(seen) + 1:04d}.json"
                    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
                    seen.add(jid)
                    report["documents"] = len(seen)
                entry["completed"] = True
                print(f"  {len(jids)} 筆；累計 {len(seen)} 份全文", flush=True)
                if interval:
                    time.sleep(interval)
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(report["error"], flush=True)
    finally:
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        report["completed_cases"] = sum(c["completed"] for c in report["cases"])
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def download_cases(cases, output, *, web=False, pdf=False, api=False, progress=None, on_result=None):
    """GUI 共用入口；背景執行緒可透過 progress 回報進度。"""
    if not any((web, pdf, api)):
        raise ValueError("請至少選擇一個下載項目")
    from judgment_app.download_judgments import get_token, get_judgment

    report = {"files": [], "empty": [], "errors": [], "skipped": [], "failed_cases": []}
    token = None
    with requests.Session() as session:
        for index, case in enumerate(cases, 1):
            if progress:
                progress(f"處理 {index}/{len(cases)}：{case['court']} {case['year']} {case['case']} {case['number']}")
            jids = search_judgments(**case, session=session)
            if api and on_result:
                for jid in jids:
                    on_result(f"已取得 JID：{jid}")
            if not jids:
                report["empty"].append(case)
                if on_result:
                    on_result(f"無符合結果或未公開：{case['court']} {case['year']}年度{case['case']}字第{case['number']}號")
            for order, jid in enumerate(jids, 1):
                name = judgment_filename(jid) + (f"_{order}" if len(jids) > 1 else "")
                for enabled, mode, suffix in ((web, "網頁全文", ".txt"), (pdf, "PDF", ".pdf"), (api, "API", ".json")):
                    if not enabled:
                        continue
                    target = Path(output) / f"{name}{suffix}"
                    if target.is_file():
                        report["skipped"].append(str(target))
                        if on_result:
                            on_result(f"檔案已存在，跳過下載 [{mode}]：{target}")
                        continue
                    try:
                        if mode == "網頁全文":
                            content = get_judgment_web(jid, session=session)["text"].encode("utf-8")
                        elif mode == "PDF":
                            content = get_judgment_pdf(jid, session=session)
                        else:
                            if on_result:
                                on_result(f"API 下載中：{jid}")
                            if token is None:
                                token = get_token()
                            content = json.dumps(get_judgment(token, jid), ensure_ascii=False, indent=2).encode("utf-8")
                        path = save_document(output, name, suffix, content)
                        report["files"].append(str(path))
                        if on_result:
                            on_result(f"下載成功 [{mode}]：{path}")
                    except requests.HTTPError as exc:
                        if exc.response is not None and exc.response.status_code in {403, 429, 503}:
                            raise
                        report["errors"].append(f"{jid} {mode}：{exc}")
                        if case not in report["failed_cases"]:
                            report["failed_cases"].append(dict(case))
                        if on_result:
                            on_result(f"下載失敗 [{mode}]：{jid}：{exc}")
                    except (RuntimeError, ValueError, ApiResponseError, requests.RequestException) as exc:
                        report["errors"].append(f"{jid} {mode}：{exc}")
                        if case not in report["failed_cases"]:
                            report["failed_cases"].append(dict(case))
                        if on_result:
                            on_result(f"下載失敗 [{mode}]：{jid}：{exc}")
    return report


def download_case_pdfs(court: str, year: int, case: str, number: int, output: str | Path) -> list[Path]:
    """輸入案號，搜尋並下載所有符合現有搜尋條件的判決 PDF。"""
    with requests.Session() as session:
        jids = search_judgments(court, year, case, number, session=session)
        paths = []
        for index, jid in enumerate(jids, 1):
            name = judgment_filename(jid) + (f"_{index}" if len(jids) > 1 else "")
            paths.append(save_document(output, name, ".pdf", get_judgment_pdf(jid, session=session)))
        return paths


def get_judgment_web(jid: str, *, session=None) -> dict[str, str]:
    """回傳 JID、來源網址、裁判正文；失敗時拋出例外，不回傳空全文。"""
    if len(jid.split(",")) < 6:
        raise ValueError(f"不完整的 JID：{jid!r}")
    with (requests.Session() if session is None else nullcontext(session)) as client:
        response = client.get(
            DOCUMENT_URL, params={"ty": "JD", "id": jid}, timeout=(10, 30)
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        body = soup.select_one(".jud_content")
        if body is None:
            raise RuntimeError(f"找不到裁判正文，可能遭阻擋或網站格式變更：{response.url}")
        for element in body.select("script, style"):
            element.decompose()
        html_content = body.select_one(".htmlcontent")
        text = webpage_text(html_content if html_content is not None else body)
        if not text:
            raise RuntimeError(f"裁判正文為空：{jid}")
        return {"jid": jid, "url": response.url, "text": text}


def get_judgment_pdf(jid: str, *, session=None) -> bytes:
    """下載網站提供的原始 PDF，不以 HTML 列印代替。"""
    with (requests.Session() if session is None else nullcontext(session)) as client:
        response = client.get(DOCUMENT_URL, params={"ty": "JD", "id": jid}, timeout=(10, 30))
        response.raise_for_status()
        link = BeautifulSoup(response.content, "html.parser").select_one("a#hlExportPDF[href]")
        if link is None:
            raise RuntimeError(f"網站未提供 PDF：{jid}")
        url = urljoin(response.url, link["href"])
        if not url.startswith("https://judgment.judicial.gov.tw/"):
            raise RuntimeError("無法識別網站 PDF 連結")
        pdf = client.get(url, headers={"Referer": response.url}, timeout=(10, 60))
        pdf.raise_for_status()
        if not pdf.content.startswith(b"%PDF-"):
            raise RuntimeError(f"下載回應不是 PDF，可能為網站錯誤頁：{jid}")
        return pdf.content


def judgment_filename(jid: str) -> str:
    court, year, case, number, *_ = jid.split(",")
    court = COURT_MAP.get(court[:3], court)
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", f"{court}{year}年度{case}字第{number}號").rstrip(". ")


def save_document(folder: str | Path, name: str, suffix: str, content: bytes) -> Path:
    """避免覆寫既有檔案；同名時追加序號。"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        path = folder / f"{name}{'_' + str(index) if index else ''}{suffix}"
        try:
            with path.open("xb") as file:
                file.write(content)
            return path
        except FileExistsError:
            index += 1


def webpage_text(element) -> str:
    """保留段落、明確換行及預格式文字；不模擬視窗寬度造成的折行。"""
    parts = []
    blocks = {"div", "p", "section", "article", "li", "tr", "h1", "h2", "h3", "pre"}

    def boundary():
        if parts and not parts[-1].endswith("\n"):
            parts.append("\n")

    def visit(node, whitespace="normal"):
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            value = str(node)
            parts.append(value if whitespace in {"pre", "pre-wrap", "break-spaces"} else re.sub(r"[\t\r\n\f ]+", " ", value))
            return
        if node.name in {"script", "style"} or node.has_attr("hidden"):
            return
        style = node.get("style", "")
        if re.search(r"display\s*:\s*none", style, re.I):
            return
        match = re.search(r"white-space\s*:\s*([\w-]+)", style, re.I)
        if match:
            whitespace = match[1].lower()
        elif node.name == "pre":
            whitespace = "pre"
        if node.name == "br":
            parts.append("\n")
            return
        if node.name in blocks:
            boundary()
        for child in node.children:
            visit(child, whitespace)
        if node.name in blocks:
            boundary()

    visit(element)
    return "".join(parts).strip(" \t\r\n")


def wrap_judgment_text(text: str, chinese_width: int = 50) -> str:
    """保留既有段落，依全形 2、半形 1 的寬度加入折行。"""
    if chinese_width < 1:
        raise ValueError("每行中文字寬度必須大於零")
    lines = []
    limit = chinese_width * 2
    for line in text.split("\n"):
        current = []
        width = 0
        for char in line:
            if unicodedata.category(char) in {"Mn", "Me", "Cf"}:
                size = 0
            elif char == "\t":
                size = 4 - width % 4
            else:
                size = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
            if current and width + size > limit:
                lines.append("".join(current))
                current = []
                width = 0
                if char == "\t":
                    size = 4
            current.append(char)
            width += size
        lines.append("".join(current))
    return "\n".join(lines)


class MeasuredSession(requests.Session):
    """記錄所有搜尋與全文請求；不自動重試，不繞過網站限制。"""

    def __init__(self, records, max_requests=1000):
        super().__init__()
        self.records = records
        self.max_requests = max_requests
        self.hooks["response"].append(self.record_response)

    def request(self, method, url, **kwargs):
        if len(self.records) >= self.max_requests:
            raise RuntimeError("已達本次測試請求上限")
        return super().request(method, url, **kwargs)

    def record_response(self, response, *args, **kwargs):
        self.records.append({
            "method": response.request.method,
            "url": response.url,
            "status": response.status_code,
            "seconds": response.elapsed.total_seconds(),
            "retry_after": response.headers.get("Retry-After"),
        })
        if response.status_code in {403, 429, 503} or response.headers.get("Retry-After"):
            raise RuntimeError(
                f"停止測試：HTTP {response.status_code}，"
                f"Retry-After={response.headers.get('Retry-After')}；"
                "可能為限流、拒絕存取或維護，需檢查紀錄。"
            )
        response.raise_for_status()


if __name__ == "__main__":
    main()
