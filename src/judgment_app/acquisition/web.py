"""透過裁判書網站讀取全文；也可執行 Excel 循序批次測試。"""

import argparse
import json
import time
import re
import logging
from urllib.parse import urljoin, urlparse, parse_qs, unquote
from email.message import Message
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Comment, NavigableString

from judgment_app.case_number import CaseNumber, JudgmentId
from judgment_app.exceptions import ApiResponseError
from judgment_app.acquisition.read_case_numbers import read_case_numbers
from judgment_app.paths import resolve_output_path

SEARCH_URL = "https://judgment.judicial.gov.tw/FJUD/Default_AD.aspx"
TIMEOUT = (10, 30)
MAX_ATTEMPTS = 3
MAX_RESULT_PAGES = 100
DOWNLOAD_ERRORS = (RuntimeError, ValueError, OSError, ApiResponseError, requests.RequestException)

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


def search_example():
    results = case_to_jids(CaseNumber("TPD", 114, "訴", 219))

    print(f"共找到 {len(results)} 筆裁判")
    for result in results:
        print(f"JID：{result}")
        print()


def case_to_jids(
    case_number: CaseNumber,
    *,
    session: requests.Session | None = None,
    on_error=None,
) -> list[JudgmentId]:
    results = []
    seen_jids = set()
    visited_pages = set()

    with (requests.Session() if session is None else nullcontext(session)) as session:
        # 1. 取得 ASP.NET 動態欄位及 Session Cookie。
        response = _request(session, "get", SEARCH_URL)
        soup = _get_soup(response)

        form = soup.find("form")
        if form is None:
            raise RuntimeError("搜尋頁沒有表單，請確認網站是否正常。")

        # 只收集 hidden，避免誤送未勾選的 jud_sys checkbox。
        payload = {
            field["name"]: field.get("value", "")
            for field in form.select('input[type="hidden"][name]')
        }

        payload.update({
            "jud_court": case_number.court,
            "jud_sys": "M",
            "jud_year": str(case_number.year),
            "sel_judword": "",
            "jud_case": case_number.case,
            "jud_no": str(case_number.number),
            "jud_no_end": "",
            "dy1": "",
            "dm1": "",
            "dd1": "",
            "dy2": "",
            "dm2": "",
            "dd2": "",
            "jud_title": "",
            "jud_jmain": "",
            "jud_kw": "",
            "KbStart": "",
            "KbEnd": "",
            "judtype": "JUDBOOK",
            "whosub": "1",
            "ctl00$cp_content$btnQry": "送出查詢",
        })

        session.cookies.set(
            "fjud_ad",
            "1",
            domain="judgment.judicial.gov.tw",
            path="/",
        )

        # 2. POST 後，q 會出現在結果 iframe 的 src。
        post_url = urljoin(response.url, form.get("action") or response.url)
        response = _request(session, "post",
            post_url,
            data=payload,
            headers={"Referer": response.url},
            timeout=TIMEOUT,
        )
        soup = _get_soup(response)

        iframe = soup.select_one("iframe#iframe-data[src]")
        if iframe is None:
            raise RuntimeError(
                "搜尋回應沒有結果 iframe，可能是網站格式變更或搜尋失敗。"
            )

        page_url = urljoin(response.url, iframe["src"])
        query = parse_qs(urlparse(page_url).query)

        if query.get("err") == ["Q003"]:
            return []

        if urlparse(page_url).path.lower().endswith("/errorpage.aspx"):
            raise RuntimeError(f"搜尋失敗：{page_url}")

        # 3. 讀取結果及後續分頁。
        try:
            while page_url:
                if len(visited_pages) >= MAX_RESULT_PAGES:
                    raise RuntimeError(f"已達搜尋分頁上限 {MAX_RESULT_PAGES}，停止以避免無限迴圈。")
                if page_url in visited_pages:
                    raise RuntimeError("分頁連結重複，停止以避免無限迴圈。")
                visited_pages.add(page_url)

                response = _request(session, "get", page_url)
                soup = _get_soup(response)

                has_document_links = False
                for link in soup.select("a[href]"):
                    link_url = urlparse(urljoin(response.url, link["href"]))

                    if not link_url.path.lower().endswith("/data.aspx"):
                        continue

                    # parse_qs 已經處理 URL decode，不需再 unquote。
                    params = parse_qs(link_url.query)
                    if params.get("ty") != ["JD"]:
                        continue

                    has_document_links = True
                    try:
                        jid = JudgmentId.from_string(params.get("id", [""])[0])
                    except ValueError as exc:
                        if on_error is None:
                            raise
                        on_error(exc)
                        continue
                    if link.get_text(" ", strip=True).endswith("判決") and jid not in seen_jids:
                        seen_jids.add(jid)
                        results.append(jid)

                # 不公開案件會顯示結果列，但不提供 data.aspx 連結。
                withheld = _withheld_results(soup)
                if not has_document_links and not withheld:
                    raise RuntimeError(
                        f"結果頁沒有裁判連結，請檢查網站回應：{response.url}"
                    )

                # 找「下一頁」，不跟隨排序或最後一頁連結。
                page_url = None
                for link in soup.select("a[href]"):
                    label = " ".join([
                        link.get_text(" ", strip=True),
                        link.get("title", ""),
                        link.get("aria-label", ""),
                        *[
                            img.get("alt", "")
                            for img in link.select("img[alt]")
                        ],
                    ])

                    if "下一頁" not in label:
                        continue
                    if (
                        link.has_attr("disabled")
                        or link.get("aria-disabled") == "true"
                        or "disabled" in link.get("class", [])
                    ):
                        continue

                    href = link["href"].strip()
                    if not href or href == "#":
                        continue
                    if href.lower().startswith("javascript:"):
                        raise RuntimeError(
                            "下一頁使用 JavaScript postback，需調整分頁處理。"
                        )

                    page_url = urljoin(response.url, href)
                    break
        except DOWNLOAD_ERRORS as exc:
            if on_error is None:
                raise
            on_error(exc)

    return results


def run_batch(excel: Path, output: Path, interval: float = 0):
    if interval < 0:
        raise ValueError("interval 不可小於零")
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
                entry = {"case": case.to_dict(), "jids": [], "completed": False, "errors": []}
                report["cases"].append(entry)
                print(f"[{index}/{len(cases)}] {case}", flush=True)

                def failed(exc):
                    message = f"{case.to_string()}：{_error_detail(exc)}"
                    entry["errors"].append(message)
                    report["error"] = message
                    print(message, flush=True)

                try:
                    jids = case_to_jids(case, session=session, on_error=failed)
                except DOWNLOAD_ERRORS as exc:
                    failed(exc)
                    if interval:
                        time.sleep(interval)
                    continue
                entry["jids"] = [jid.to_string() for jid in jids]
                for jid in jids:
                    if jid in seen:
                        continue
                    try:
                        document = get_judgment_web(jid, session=session)
                        # 使用序號作檔名，JID 留在 JSON，避免不合法的路徑字元。
                        save_document(output, f"judgment_{len(seen) + 1:04d}", ".json",
                                      json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))
                    except DOWNLOAD_ERRORS as exc:
                        failed(RuntimeError(f"{jid.to_string()}：{_error_detail(exc)}"))
                        continue
                    seen.add(jid)
                    report["documents"] = len(seen)
                entry["completed"] = not entry["errors"]
                print(f"  {len(jids)} 筆；累計 {len(seen)} 份全文", flush=True)
                if interval:
                    time.sleep(interval)
    except DOWNLOAD_ERRORS as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(report["error"], flush=True)
    finally:
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        report["completed_cases"] = sum(c["completed"] for c in report["cases"])
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def download_cases(cases: list[CaseNumber], output, *, web=False, pdf=False, api=False, history=False, progress=None, on_result=None):
    """GUI 共用入口；背景執行緒可透過 progress 回報進度。"""
    if not any((web, pdf, api)):
        raise ValueError("請至少選擇一個下載項目")
    from judgment_app.acquisition.api import get_token, get_judgment

    report = {"files": [], "empty": [], "errors": [], "skipped": [], "failed_cases": [], "history_files": [], "history_skipped": [],
              "txt_to_pdf": [], "pdf_to_txt": [], "missing_formats": [],
              "skipped_documents": [], "history_skipped_documents": []}
    history = history and (web or pdf)
    token = None
    with requests.Session() as session:
        session._download_progress = on_result
        for index, case in enumerate(cases, 1):
            if progress:
                progress(f"處理 {index}/{len(cases)}：{case.to_string(ch=False)}")
            try:
                jids = case_to_jids(case, session=session, on_error=lambda exc: _record_download_error(
                    report, case, f"搜尋失敗：{case.to_string()}", exc, on_result))
            except DOWNLOAD_ERRORS as exc:
                _record_download_error(report, case, f"搜尋失敗：{case.to_string()}", exc, on_result)
                continue
            if api and on_result:
                for jid in jids:
                    on_result(f"已取得 JID：{jid.to_string()}")
            if not jids and case not in report["failed_cases"]:
                report["empty"].append(case)
                if on_result:
                    on_result(f"無符合結果或未公開：{case.to_string()}")
            folder = Path(output) / case.file_name if history else Path(output)
            history_seen = set(jids)
            for jid in jids:
                name = jid.file_name
                available_formats = set()
                for enabled, mode, suffix in ((web, "網頁全文", ".txt"), (pdf, "PDF", ".pdf"), (api, "API", ".json")):
                    if not enabled:
                        continue
                    target = folder / f"{name}{suffix}"
                    try:
                        if target.is_file():
                            available_formats.add(suffix)
                            _record_skipped_document(report, jid, target)
                            if on_result:
                                on_result(f"檔案已存在，跳過下載 [{mode}]：{target}")
                            continue
                        if on_result:
                            on_result(f"下載中 [{mode}]：{jid.to_string()}")
                        if mode in {"網頁全文", "PDF"}:
                            path, skipped = _save_web_document(jid, folder, name, session, on_result, pdf=mode == "PDF")
                            available_formats.add(path.suffix)
                            if skipped:
                                _record_skipped_document(report, jid, path)
                            else:
                                report["files"].append(str(path))
                            if on_result:
                                on_result(f"{'檔案已存在，跳過下載' if skipped else '下載成功'} [{mode}]：{path}")
                            continue
                        else:
                            if on_result:
                                on_result(f"API 下載中：{jid.to_string()}")
                            if token is None:
                                token = get_token()
                        content = json.dumps(get_judgment(token, jid.to_string()), ensure_ascii=False, indent=2).encode("utf-8")
                        path = save_document(folder, name, suffix, content)
                        report["files"].append(str(path))
                        if on_result:
                            on_result(f"下載成功 [{mode}]：{path}")
                    except DOWNLOAD_ERRORS as exc:
                        _record_download_error(report, case, f"下載失敗 [{mode}]：{jid.to_string()}", exc, on_result)
                _record_download_formats(report, jid, available_formats, web=web, pdf=pdf)
                if history:
                    download_history(jid, case, folder, session, history_seen, report, on_result, web=web, pdf=pdf)
    return report


def download_history(jid: JudgmentId, original: CaseNumber, folder, session, seen, report, on_result, *, web, pdf):
    def log(message):
        if on_result:
            on_result(message)

    def failed(exc, related=None, mode="歷審清單"):
        if related:
            court, year, word, number, *_ = related.to_string().split(",")
            case = CaseNumber(court, int(year), word, int(number), original)
        else:
            case = original
        context = f"歷審下載失敗 [{mode}]：{related.to_string() if related is not None else jid.to_string()}（原始案號：{original.file_name}）"
        _record_download_error(report, case, context, exc, log)

    log(f"查詢歷審裁判：{original.file_name}")
    try:
        related_jids = get_judgment_history(jid, session=session, on_error=failed)
    except DOWNLOAD_ERRORS as exc:
        failed(exc)
        return
    log(f"找到 {len(related_jids)} 份歷審判決（原始案號：{original.file_name}）")
    for related in related_jids:
        if related in seen:
            continue
        seen.add(related)
        # 日期與版本區分同案號的多份判決，重跑時檔名保持固定。
        name = related.file_name
        available_formats = set()
        for enabled, mode, suffix in ((web, "網頁全文", ".txt"), (pdf, "PDF", ".pdf")):
            if not enabled:
                continue
            target = folder / f"{name}{suffix}"
            log(f"歷審下載中 [{mode}]：{related.file_name}（原始案號：{original.file_name}）")
            try:
                if target.is_file():
                    available_formats.add(suffix)
                    _record_skipped_document(report, related, target, original)
                    log(f"歷審檔案已存在，跳過下載 [{mode}]：{target}")
                    continue
                path, skipped = _save_web_document(related, folder, name, session, log, pdf=mode == "PDF")
                available_formats.add(path.suffix)
                if skipped:
                    _record_skipped_document(report, related, path, original)
                else:
                    report["history_files"].append(str(path))
                log(f"{'歷審檔案已存在，跳過下載' if skipped else '歷審下載成功'} [{mode}]：{path}")
            except DOWNLOAD_ERRORS as exc:
                failed(exc, related, mode)
        _record_download_formats(report, related, available_formats, web=web, pdf=pdf, original=original)


def download_case_pdfs(case_number: CaseNumber, output: str | Path, *, on_result=None) -> list[Path]:
    """輸入案號，搜尋並下載所有符合現有搜尋條件的判決 PDF。"""
    with requests.Session() as session:
        session._download_progress = on_result
        jids = case_to_jids(case_number, session=session)
        paths = []
        for jid in jids:
            name = jid.file_name
            try:
                paths.append(save_document(output, name, ".pdf", get_judgment_pdf(jid, session=session)))
            except DOWNLOAD_ERRORS as exc:
                message = f"下載失敗 [PDF]：{jid.to_string()}：{_error_detail(exc)}"
                (on_result or logging.getLogger(__name__).warning)(message)
        return paths


def get_judgment_history(jid: JudgmentId, *, session, on_error=None) -> list[JudgmentId]:
    """讀取歷審區塊及網站用來填入該區塊的 AJAX 清單，只回傳判決。"""
    response = _request(session, "get", DOCUMENT_URL, params={"ty": "JD", "id": jid.to_string()})
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    panel = soup.select_one(".rela-area #JudHis")
    if panel is None:
        raise RuntimeError("找不到歷審裁判區塊")
    entries = [{"desc": a.get_text(" ", strip=True), "href": a["href"]}
               for a in panel.select("ul a[href]")]
    for script in soup.select("script"):
        match = re.search(r"[\"']([^\"']*GetJudHistory\.ashx\?[^\"']*)[\"']", script.get_text())
        if match:
            url = urljoin(response.url, match[1])
            if urlparse(url).netloc != urlparse(DOCUMENT_URL).netloc:
                raise RuntimeError("無法識別歷審清單連結")
            result = _request(session, "get", url)
            result.raise_for_status()
            data = result.json()
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                raise RuntimeError("歷審清單格式錯誤")
            entries.extend(data["list"])
            break
    else:
        if not entries and "本件無歷審裁判" not in panel.get_text():
            raise RuntimeError("找不到歷審清單載入連結")
    jids = []
    for entry in entries:
        try:
            if not isinstance(entry, dict) or not isinstance(entry.get("desc"), str) or not isinstance(entry.get("href"), str):
                raise RuntimeError("歷審項目格式錯誤")
            if not re.search(r"判決(?:\s*[（(].*[）)])?\s*$", entry.get("desc", "")):
                continue
            if not entry.get("href"):
                continue
            url = urlparse(urljoin(response.url, entry["href"]))
            params = parse_qs(url.query)
            if url.netloc != urlparse(DOCUMENT_URL).netloc or not url.path.lower().endswith("/data.aspx") or params.get("ty") != ["JD"]:
                raise RuntimeError("無法識別歷審裁判連結")
            related = params.get("id", [""])[0]
            related = JudgmentId.from_string(related)
            if related != jid and related not in jids:
                jids.append(related)
        except (RuntimeError, ValueError) as exc:
            if on_error is None:
                raise
            on_error(exc)

    return jids


def get_judgment_web(jid: JudgmentId, *, session=None) -> dict[str, str]:
    """回傳 JID、來源網址、裁判正文；失敗時拋出例外，不回傳空全文。"""
    jid_str = jid.to_string()
    with (requests.Session() if session is None else nullcontext(session)) as client:
        response = _request(client, "get",
            DOCUMENT_URL, params={"ty": "JD", "id": jid_str}, timeout=(10, 30)
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        download_link = _large_judgment_download_link(soup)
        if download_link is not None:
            url = urljoin(response.url, download_link["href"])
            if urlparse(url).path.lower().endswith(".pdf"):
                raise PdfOnlyJudgment(url, response.url)
            suffix, content = _download_judgment_file(url, response.url, jid, client)
            if suffix == ".pdf":
                raise PdfOnlyJudgment(url, response.url, content)
            return {"jid": jid_str, "url": response.url, "text": content.decode("utf-8")}
        body = soup.select_one(".jud_content")
        if body is None:
            raise RuntimeError(f"找不到裁判正文，可能遭阻擋或網站格式變更：{response.url}")
        for element in body.select("script, style"):
            element.decompose()
        html_content = body.select_one(".htmlcontent")
        text = webpage_text(html_content) if html_content is not None else ""
        if not text:
            text_content = body.select_one(".text-pre")
            text = webpage_text(text_content if text_content is not None else body)
        if not text:
            raise RuntimeError(f"裁判正文為空：{jid_str}")
        return {"jid": jid_str, "url": response.url, "text": text}


def get_judgment_pdf(jid: JudgmentId, *, session=None) -> bytes:
    """下載網站提供的原始 PDF，不以 HTML 列印代替。"""
    jid_str = jid.to_string()
    with (requests.Session() if session is None else nullcontext(session)) as client:
        response = _request(client, "get", DOCUMENT_URL, params={"ty": "JD", "id": jid_str})
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        link = _large_judgment_download_link(soup)
        if link is None:
            link = soup.select_one("a#hlExportPDF[href]")
        if link is None:
            raise RuntimeError(f"網站未提供 PDF：{jid_str}")
        url = urljoin(response.url, link["href"])
        return _download_pdf(url, response.url, jid, client)


def save_document(folder: str | Path, name: str, suffix: str, content: bytes) -> Path:
    """避免覆寫既有檔案；同名時追加序號。"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        path = folder / f"{name}{'_' + str(index) if index else ''}{suffix}"
        try:
            file = path.open("xb")
        except FileExistsError:
            index += 1
            continue
        try:
            with file as stream:
                stream.write(content)
            return path
        except BaseException as exc:
            # 本次建立的半成品不可讓下次下載誤認成已完成；不碰既有檔案。
            try:
                path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                exc.add_note(f"無法移除未完成檔案 {path}：{cleanup_error}")
            raise


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
        elif node.name == "pre" or "text-pre" in node.get("class", []):
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


'''def wrap_judgment_text(text: str, chinese_width: int = 50) -> str:
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
    return "\n".join(lines)'''


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


class PdfOnlyJudgment(RuntimeError):
    """全文超過網頁上限，須改下載原始 PDF。"""

    def __init__(self, pdf_url, page_url, content=None):
        super().__init__(f"裁判書全文大於 1 M，僅提供 PDF：{pdf_url}")
        self.pdf_url = pdf_url
        self.page_url = page_url
        self.content = content


class TextOnlyJudgment(RuntimeError):
    """網站的全文下載連結實際提供 TXT，不能另存成 PDF。"""

    def __init__(self, url, content):
        super().__init__(f"網站僅提供 TXT 全文：{url}")
        self.content = content


def _large_judgment_download_link(soup):
    body = soup.select_one(".jud_content")
    if body is None:
        body = soup
    if not re.search(r"裁判書全文大於\s*1\s*M", body.get_text(" ", strip=True), re.I):
        return None
    for link in body.select("a[href]"):
        path = urlparse(link["href"]).path.lower()
        if path.endswith((".pdf", ".txt", "/getoverlimit.ashx")) or path == "getoverlimit.ashx":
            return link
    raise RuntimeError("裁判書全文大於 1 M，但找不到全文下載連結（PDF／TXT）")


def _download_pdf(url, referer, jid, session):
    suffix, content = _download_judgment_file(url, referer, jid, session)
    if suffix == ".txt":
        raise TextOnlyJudgment(url, content)
    return content


def _download_judgment_file(url, referer, jid, session):
    if not url.startswith("https://judgment.judicial.gov.tw/"):
        raise RuntimeError(f"無法識別網站全文下載連結：{url}")
    response = _request(session, "get", url, headers={"Referer": referer}, timeout=(10, 60))
    response.raise_for_status()
    if response.content.startswith(b"%PDF-"):
        return ".pdf", response.content
    disposition = Message()
    disposition["Content-Disposition"] = response.headers.get("Content-Disposition", "")
    filename = unquote(disposition.get_filename() or "")
    if filename.lower().endswith(".txt") or urlparse(url).path.lower().endswith(".txt"):
        try:
            text = response.content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"全文文字檔不是 UTF-8：{jid.to_string()}；{url}") from exc
        if not text.strip() or re.match(r"\s*<(?:!doctype\s+html|html|head|body)\b", text, re.I):
            raise RuntimeError(f"全文文字檔為空或網站錯誤頁：{jid.to_string()}；{url}")
        return ".txt", text.encode("utf-8")
    raise RuntimeError(f"下載回應不是 PDF 或可辨識的 TXT，可能為網站錯誤頁：{jid.to_string()}；{url}")


def _record_skipped_document(report, jid, path, original=None):
    """保留原有檔案計數，另記錄可顯示的案號與實際跳過格式。"""
    key = "history_skipped" if original is not None else "skipped"
    report[key].append(str(path))
    report[f"{key}_documents"].append({
        "jid": jid, "original_case": original,
        "skipped_format": path.suffix.lstrip(".").upper(),
    })


def _record_download_formats(report, jid, available, *, web, pdf, original=None):
    """依單份判決的實際格式分類；已存在並跳過的檔案也算取得。"""
    entry = {"jid": jid, "original_case": original}
    if web and pdf:
        missing = [label for suffix, label in ((".txt", "TXT"), (".pdf", "PDF")) if suffix not in available]
        if missing:
            report["missing_formats"].append({**entry, "missing_formats": missing})
    elif web and ".pdf" in available and ".txt" not in available:
        report["txt_to_pdf"].append(entry)
    elif pdf and ".txt" in available and ".pdf" not in available:
        report["pdf_to_txt"].append(entry)


def _save_web_document(jid, folder, name, session, on_result, *, pdf=False):
    suffix = ".pdf" if pdf else ".txt"
    try:
        content = get_judgment_pdf(jid, session=session) if pdf else get_judgment_web(jid, session=session)["text"].encode("utf-8")
    except PdfOnlyJudgment as exc:
        suffix = ".pdf"
        if on_result:
            on_result(f"裁判書全文大於 1 M，改下載原始 PDF：{jid.to_string()}")
        target = Path(folder) / f"{name}{suffix}"
        if target.is_file():
            return target, True
        content = exc.content if exc.content is not None else _download_pdf(exc.pdf_url, exc.page_url, jid, session)
    except TextOnlyJudgment as exc:
        suffix = ".txt"
        if on_result:
            on_result(f"網站提供的是 TXT 全文，改存文字檔：{jid.to_string()}")
        target = Path(folder) / f"{name}{suffix}"
        if target.is_file():
            return target, True
        content = exc.content
    return save_document(folder, name, suffix, content), False


def _get_soup(response):
    response.raise_for_status()
    return BeautifulSoup(response.content, "html.parser")


def _withheld_results(soup):
    """只將明確的不公開說明視為無全文；空頁與阻擋頁仍應回報錯誤。"""
    if "本件經程式判定為依法不得公開或須去識別化後公開之案件" in soup.get_text():
        return True
    table = soup.select_one("table#jud")
    if table is None:
        return False
    titles = table.select("a#hlTitle, a.hlTitle_scroll")
    return bool(titles) and all(not title.get("href") for title in titles) and any(
        phrase in table.get_text() for phrase in ("遮隱本件裁判書全文", "依法不得公開", "不予公開")
    )


def _error_detail(exc):
    parts = [f"{type(exc).__name__}: {exc}"]
    parts.extend(getattr(exc, "__notes__", []))
    cause = exc.__cause__
    if cause is not None:
        parts.append(f"原因：{type(cause).__name__}: {cause}")
    response = getattr(exc, "response", None)
    if response is not None and response.headers.get("Retry-After"):
        parts.append(f"Retry-After={response.headers['Retry-After']}")
    return "；".join(parts)


def _record_download_error(report, case, context, exc, on_result):
    message = f"{context}：{_error_detail(exc)}"
    report["errors"].append(message)
    if case not in report["failed_cases"]:
        report["failed_cases"].append(case)
    if on_result:
        on_result(message)


def _request(session, method, url, **kwargs):
    """搜尋與讀取皆不修改遠端資料；暫時性錯誤最多嘗試三次。"""
    kwargs.setdefault("timeout", TIMEOUT)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = getattr(session, method)(url, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.SSLError:
            raise
        except requests.HTTPError as exc:
            response = exc.response
            # 拒絕存取、限流或要求等待時不在此立即重送。
            if (response is None or response.status_code not in {500, 502, 503, 504}
                    or response.headers.get("Retry-After")):
                raise
            error = exc
        except (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError) as exc:
            error = exc
        if attempt == MAX_ATTEMPTS:
            raise error
        delay = 2 ** (attempt - 1)
        progress = session.__dict__.get("_download_progress")
        if progress:
            progress(f"請求失敗，{delay} 秒後重試（{attempt + 1}/{MAX_ATTEMPTS}）：{url}；{_error_detail(error)}")
        time.sleep(delay)


if __name__ == "__main__":
    main()
