"""透過裁判書網站讀取全文；也可執行 Excel 循序批次測試。"""

import argparse
import json
import time
import re
from urllib.parse import urljoin, urlparse, parse_qs
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Comment, NavigableString

from judgment_app.case_number import CaseNumber, JudgmentId
from judgment_app.exceptions import ApiResponseError
from judgment_app.read_case_numbers import read_case_numbers
from judgment_app.paths import resolve_output_path

SEARCH_URL = "https://judgment.judicial.gov.tw/FJUD/Default_AD.aspx"
TIMEOUT = (10, 30)

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
) -> list[JudgmentId]:
    results = []
    seen_jids = set()
    visited_pages = set()

    with (requests.Session() if session is None else nullcontext(session)) as session:
        # 1. 取得 ASP.NET 動態欄位及 Session Cookie。
        response = session.get(SEARCH_URL, timeout=TIMEOUT)
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
        response = session.post(
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
        while page_url:
            if page_url in visited_pages:
                raise RuntimeError("分頁連結重複，停止以避免無限迴圈。")
            visited_pages.add(page_url)

            response = session.get(page_url, timeout=TIMEOUT)
            soup = _get_soup(response)

            page_jids = []
            for link in soup.select("a[href]"):
                link_url = urlparse(urljoin(response.url, link["href"]))

                if not link_url.path.lower().endswith("/data.aspx"):
                    continue

                # parse_qs 已經處理 URL decode，不需再 unquote。
                params = parse_qs(link_url.query)
                if params.get("ty") != ["JD"]:
                    continue

                jid = JudgmentId.from_string(params.get("id", [""])[0])
                page_jids.append(jid)
                if link.get_text(" ", strip=True).endswith("判決") and jid not in seen_jids:
                    seen_jids.add(jid)
                    results.append(
                        #"jid": jid,
                        #"date": fields[4],
                        jid,
                    )

            # 不公開案件會顯示結果列，但不提供 data.aspx 連結。
            withheld = "本件經程式判定為依法不得公開或須去識別化後公開之案件" in soup.get_text()
            if not page_jids and not withheld:
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

    return results


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
                entry = {"case": case.to_dict(), "jids": [], "completed": False}
                report["cases"].append(entry)
                print(f"[{index}/{len(cases)}] {case}", flush=True)
                jids = case_to_jids(case, session=session)
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


def download_cases(cases: list[CaseNumber], output, *, web=False, pdf=False, api=False, history=False, progress=None, on_result=None):
    """GUI 共用入口；背景執行緒可透過 progress 回報進度。"""
    if not any((web, pdf, api)):
        raise ValueError("請至少選擇一個下載項目")
    from judgment_app.download_judgments import get_token, get_judgment

    report = {"files": [], "empty": [], "errors": [], "skipped": [], "failed_cases": [], "history_files": [], "history_skipped": []}
    history = history and (web or pdf)
    token = None
    with requests.Session() as session:
        for index, case in enumerate(cases, 1):
            if progress:
                progress(f"處理 {index}/{len(cases)}：{case.to_string(ch=False)}")
            jids = case_to_jids(case, session=session)
            if api and on_result:
                for jid in jids:
                    on_result(f"已取得 JID：{jid.to_string()}")
            if not jids:
                report["empty"].append(case)
                if on_result:
                    on_result(f"無符合結果或未公開：{case.to_string()}")
            folder = Path(output) / case.file_name if history else Path(output)
            history_seen = set(jids)
            for order, jid in enumerate(jids, 1):
                name = jid.file_name + (f"_{order}" if len(jids) > 1 else "")
                for enabled, mode, suffix in ((web, "網頁全文", ".txt"), (pdf, "PDF", ".pdf"), (api, "API", ".json")):
                    if not enabled:
                        continue
                    target = folder / f"{name}{suffix}"
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
                                on_result(f"API 下載中：{jid.to_string()}")
                            if token is None:
                                token = get_token()
                            content = json.dumps(get_judgment(token, jid), ensure_ascii=False, indent=2).encode("utf-8")
                        path = save_document(folder, name, suffix, content)
                        report["files"].append(str(path))
                        if on_result:
                            on_result(f"下載成功 [{mode}]：{path}")
                    except requests.HTTPError as exc:
                        if exc.response is not None and exc.response.status_code in {403, 429, 503}:
                            raise
                        report["errors"].append(f"{jid.to_string()} {mode}：{exc}")
                        if case not in report["failed_cases"]:
                            report["failed_cases"].append(case)
                        if on_result:
                            on_result(f"下載失敗 [{mode}]：{jid.to_string()}：{exc}")
                    except (RuntimeError, ValueError, ApiResponseError, requests.RequestException) as exc:
                        report["errors"].append(f"{jid.to_string()} {mode}：{exc}")
                        if case not in report["failed_cases"]:
                            report["failed_cases"].append(case)
                        if on_result:
                            on_result(f"下載失敗 [{mode}]：{jid.to_string()}：{exc}")
                if history:
                    download_history(jid, case, folder, session, history_seen, report, on_result, web=web, pdf=pdf)
    return report


def download_history(jid: JudgmentId, original: CaseNumber, folder, session, seen, report, on_result, *, web, pdf):
    def log(message):
        if on_result:
            on_result(message)

    def failed(exc, related=None, mode="歷審清單"):
        if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code in {403, 429, 503}:
            raise exc
        if related:
            court, year, word, number, *_ = related.to_string().split(",")
            case = CaseNumber(court, int(year), word, int(number), original)
        else:
            case = original
        if case not in report["failed_cases"]:
            report["failed_cases"].append(case)
        message = f"歷審下載失敗 [{mode}]：{related.to_string() or jid.to_string()}（原始案號：{original.file_name}）：{exc}"
        report["errors"].append(message)
        log(message)

    log(f"查詢歷審裁判：{original.file_name}")
    try:
        related_jids = get_judgment_history(jid, session=session)
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        failed(exc)
        return
    log(f"找到 {len(related_jids)} 份歷審判決（原始案號：{original.file_name}）")
    for related in related_jids:
        if related in seen:
            continue
        seen.add(related)
        # 日期與版本區分同案號的多份判決，重跑時檔名保持固定。
        name = related.file_name + "_" + re.sub(r"[^0-9A-Za-z_-]", "_", "_".join(related.to_string().split(",")[4:]))
        for enabled, mode, suffix in ((web, "網頁全文", ".txt"), (pdf, "PDF", ".pdf")):
            if not enabled:
                continue
            target = folder / f"{name}{suffix}"
            if target.is_file():
                report["history_skipped"].append(str(target))
                log(f"歷審檔案已存在，跳過下載 [{mode}]：{target}")
                continue
            log(f"歷審下載中 [{mode}]：{related.file_name}（原始案號：{original.file_name}）")
            try:
                if mode == "網頁全文":
                    content = get_judgment_web(related, session=session)["text"].encode("utf-8")
                else:
                    content = get_judgment_pdf(related, session=session)
                path = save_document(folder, name, suffix, content)
                report["history_files"].append(str(path))
                log(f"歷審下載成功 [{mode}]：{path}")
            except (RuntimeError, ValueError, OSError, requests.RequestException) as exc:
                failed(exc, related, mode)


def download_case_pdfs(case_number: CaseNumber, output: str | Path) -> list[Path]:
    """輸入案號，搜尋並下載所有符合現有搜尋條件的判決 PDF。"""
    with requests.Session() as session:
        jids = case_to_jids(case_number, session=session)
        paths = []
        for index, jid in enumerate(jids, 1):
            name = jid.file_name + (f"_{index}" if len(jids) > 1 else "")
            paths.append(save_document(output, name, ".pdf", get_judgment_pdf(jid, session=session)))
        return paths


def get_judgment_history(jid: JudgmentId, *, session) -> list[JudgmentId]:
    """讀取歷審區塊及網站用來填入該區塊的 AJAX 清單，只回傳判決。"""
    response = session.get(DOCUMENT_URL, params={"ty": "JD", "id": jid.to_string()}, timeout=(10, 30))
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
            result = session.get(url, timeout=(10, 30))
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
        if not isinstance(entry, dict):
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
    return jids


def get_judgment_web(jid: JudgmentId, *, session=None) -> dict[str, str]:
    """回傳 JID、來源網址、裁判正文；失敗時拋出例外，不回傳空全文。"""
    jid_str = jid.to_string()
    with (requests.Session() if session is None else nullcontext(session)) as client:
        response = client.get(
            DOCUMENT_URL, params={"ty": "JD", "id": jid_str}, timeout=(10, 30)
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
            raise RuntimeError(f"裁判正文為空：{jid_str}")
        return {"jid": jid_str, "url": response.url, "text": text}


def get_judgment_pdf(jid: JudgmentId, *, session=None) -> bytes:
    """下載網站提供的原始 PDF，不以 HTML 列印代替。"""
    jid_str = jid.to_string()
    with (requests.Session() if session is None else nullcontext(session)) as client:
        response = client.get(DOCUMENT_URL, params={"ty": "JD", "id": jid_str}, timeout=(10, 30))
        response.raise_for_status()
        link = BeautifulSoup(response.content, "html.parser").select_one("a#hlExportPDF[href]")
        if link is None:
            raise RuntimeError(f"網站未提供 PDF：{jid_str}")
        url = urljoin(response.url, link["href"])
        if not url.startswith("https://judgment.judicial.gov.tw/"):
            raise RuntimeError("無法識別網站 PDF 連結")
        pdf = client.get(url, headers={"Referer": response.url}, timeout=(10, 60))
        pdf.raise_for_status()
        if not pdf.content.startswith(b"%PDF-"):
            raise RuntimeError(f"下載回應不是 PDF，可能為網站錯誤頁：{jid_str}")
        return pdf.content


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


def _get_soup(response):
    response.raise_for_status()
    return BeautifulSoup(response.content, "html.parser")


if __name__ == "__main__":
    main()
