"""Loopback-only HTTP interface to the existing Excel and download workflow.

Job event IDs start at 1. ``after=N`` returns events with IDs greater than N;
clients pass ``next_cursor`` on their next poll. A progress callback announces
the next case, so only the preceding cases count as completed until the job ends.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import re
import secrets
from tempfile import TemporaryDirectory
import threading
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4
import webbrowser

import pandas as pd

from judgment_app.acquisition.read_case_numbers import detect_case_number_layout, read_case_numbers
from judgment_app.acquisition.web import download_cases
from judgment_app.case_number import COURT_MAP, SPECIAL_COURT_ALIASES, CaseNumber, JudgmentId
from judgment_app.paths import resolve_output_path


LOGGER = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_CASES = 50000
STATIC_FILES = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                "/styles.css": ("styles.css", "text/css")}


def main() -> None:
    parser = argparse.ArgumentParser(description="裁判書下載工作臺：在本機瀏覽器操作。")
    parser.add_argument("--port", type=int, default=8765, help="本機連接埠（預設 8765）")
    parser.add_argument("--no-browser", action="store_true", help="啟動時不要自動開啟瀏覽器")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("連接埠必須介於 0 與 65535。")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        server = create_server(port=args.port)
    except OSError as exc:
        parser.exit(1, f"無法啟動本機網頁服務：{exc}\n若連接埠已被使用，請改用 --port 8766。\n")
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"裁判書下載工作臺：{url}\n按 Ctrl+C 停止服務。", flush=True)
    if not args.no_browser:
        timer = threading.Timer(0.3, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n本機網頁服務已停止。", flush=True)
    finally:
        server.server_close()


def create_server(port: int = 8765, *, output_dir: str | Path | None = None) -> LocalHTTPServer:
    """Create a loopback-only server; port 0 is useful for tests and free ports."""
    return LocalHTTPServer(port, output_dir=output_dir)


class RequestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class _Workbook:
    path: Path
    filename: str
    sheets: list[str]


@dataclass
class _DownloadJob:
    job_id: str
    output: Path
    total: int
    status: str = "running"
    completed_cases: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] | None = None
    error: str | None = None


class ApplicationState:
    def __init__(self, output_dir: str | Path | None = None):
        self.request_token = secrets.token_urlsafe(32)
        self.output_dir = resolve_output_path(output_dir or "data/web_downloads").resolve()
        self.uploads = TemporaryDirectory(prefix="judgment-web-")
        self.workbooks: dict[str, _Workbook] = {}
        self.jobs: dict[str, _DownloadJob] = {}
        self.lock = threading.RLock()

    def config(self) -> dict[str, Any]:
        courts = []
        for code, name in COURT_MAP.items():
            if not code:
                continue
            keywords = {code, name, f"{name} ({code})",
                        name.replace("地方法院", "地院").replace("高等法院", "高院"),
                        *SPECIAL_COURT_ALIASES.get(name, ())}
            if name.startswith(("臺灣", "福建")) and name.endswith("地方法院"):
                keywords.update({name[2:], name[2:].replace("地方法院", "地院")})
            if code == "TPD":
                keywords.add("北院")
            courts.append({"code": code, "name": name, "keywords": sorted(keywords)})
        return {"courts": courts, "defaults": {"web": True, "pdf": False, "api": False,
                "history": True, "output": str(self.output_dir)}, "request_token": self.request_token}

    def add_workbook(self, filename: str, content: bytes) -> dict[str, Any]:
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        suffix = Path(filename).suffix.lower()
        if suffix not in {".xls", ".xlsx"}:
            raise RequestError(400, "請選擇 .xls 或 .xlsx Excel 檔案。")
        workbook_id = uuid4().hex
        path = Path(self.uploads.name) / f"{workbook_id}{suffix}"
        path.write_bytes(content)
        try:
            with pd.ExcelFile(path) as workbook:
                sheets = list(workbook.sheet_names)
            if not sheets:
                raise ValueError("Excel 檔案沒有可讀取的工作表。")
        except Exception as exc:
            path.unlink(missing_ok=True)
            LOGGER.warning("Failed to read uploaded workbook %r", filename, exc_info=True)
            raise RequestError(400, f"無法讀取 Excel：{exc}") from exc
        with self.lock:
            self.workbooks[workbook_id] = _Workbook(path, filename, sheets)
        return {"workbook_id": workbook_id, "filename": filename, "sheets": sheets}

    def workbook(self, payload: dict[str, Any]) -> tuple[Path, str]:
        workbook_id = payload.get("workbook_id")
        if not isinstance(workbook_id, str):
            raise RequestError(400, "請先選擇 Excel 檔案。")
        with self.lock:
            workbook = self.workbooks.get(workbook_id)
        if workbook is None:
            raise RequestError(404, "找不到暫存的 Excel；請重新選擇檔案。")
        sheet = payload.get("sheet")
        if not isinstance(sheet, str) or sheet not in workbook.sheets:
            raise RequestError(400, "請選擇此 Excel 中的工作表。")
        return workbook.path, sheet

    def start_download(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_cases = payload.get("cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            raise RequestError(400, "請先加入要下載的案號。")
        if len(raw_cases) > MAX_CASES:
            raise RequestError(400, f"單次最多下載 {MAX_CASES:,} 個案號。")
        cases = list(dict.fromkeys(_case_number(item, positive=False) for item in raw_cases))
        options = {}
        for key, default in (("web", True), ("pdf", False), ("api", False), ("history", True)):
            value = payload.get(key, default)
            if not isinstance(value, bool):
                raise RequestError(400, "下載選項必須為布林值。")
            options[key] = value
        if not any(options[key] for key in ("web", "pdf", "api")):
            raise RequestError(400, "請至少選擇一種下載格式。")
        destination = payload.get("output", str(self.output_dir))
        if not isinstance(destination, str) or not destination.strip():
            raise RequestError(400, "請指定下載儲存路徑。")
        with self.lock:
            if any(job.status == "running" for job in self.jobs.values()):
                raise RequestError(409, "已有下載正在執行，請等待完成。")
            job_id = uuid4().hex
            root = resolve_output_path(destination.strip()).resolve()
            output = root / f"{datetime.now():%Y%m%d_%H%M%S}_{job_id[:8]}"
            try:
                output.mkdir(parents=True, exist_ok=False)
            except OSError as exc:
                LOGGER.warning("Failed to create download folder %s", output, exc_info=True)
                raise RequestError(400, f"無法建立下載資料夾：{exc}") from exc
            job = _DownloadJob(job_id=job_id, output=output, total=len(cases))
            self.jobs[job_id] = job
            worker = threading.Thread(target=self._run_download, args=(job, cases, options), daemon=True,
                                      name=f"judgment-download-{job_id[:8]}")
            worker.start()
        return {"job_id": job_id, "output": str(output)}

    def job_snapshot(self, job_id: str, after: int) -> dict[str, Any]:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise RequestError(404, "找不到此下載工作。")
            return {"status": job.status, "events": [event.copy() for event in job.events if event["id"] > after],
                    "next_cursor": len(job.events), "total": job.total, "completed_cases": job.completed_cases,
                    "report": job.report, "error": job.error, "output": str(job.output)}

    def _run_download(self, job: _DownloadJob, cases: list[CaseNumber], options: dict[str, bool]) -> None:
        def event(kind: str, message: str) -> None:
            with self.lock:
                job.events.append({"id": len(job.events) + 1, "kind": kind, "message": str(message)})
                if kind == "progress":
                    match = re.match(r"處理 ([0-9]+)/([0-9]+)：", str(message))
                    if match and int(match[2]) == job.total:
                        job.completed_cases = max(job.completed_cases, min(job.total, int(match[1]) - 1))

        try:
            report = download_cases(cases, job.output, **options,
                                    progress=lambda message: event("progress", message),
                                    on_result=lambda message: event("result", message))
            serialized = _serialize(report)
            with self.lock:
                job.report = serialized
                job.completed_cases = job.total
                job.status = "completed"
        except Exception as exc:
            LOGGER.exception("Download job %s failed", job.job_id)
            with self.lock:
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = "failed"
                event("result", f"下載中斷：{job.error}")


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, *, output_dir: str | Path | None = None):
        self.state = ApplicationState(output_dir)
        try:
            super().__init__(("127.0.0.1", port), RequestHandler)
        except Exception:
            self.state.uploads.cleanup()
            raise

    def server_close(self) -> None:
        super().server_close()
        self.state.uploads.cleanup()


class RequestHandler(BaseHTTPRequestHandler):
    server: LocalHTTPServer
    server_version = "JudgmentWeb/1.0"

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("%s %s", self.client_address[0], format % args)

    def _dispatch(self, method: str) -> None:
        try:
            self.connection.settimeout(30)
            self._check_request_origin()
            if method == "POST":
                token = self.headers.get("X-Request-Token", "")
                if not secrets.compare_digest(token, self.server.state.request_token):
                    raise RequestError(403, "頁面授權已失效，請重新整理頁面。")
            route = urlsplit(self.path)
            if method == "GET":
                self._get(route.path, route.query)
            else:
                self._post(route.path)
        except RequestError as exc:
            self._json(exc.status, {"error": str(exc)})
        except (ValueError, TypeError, KeyError) as exc:
            LOGGER.warning("Invalid request %s", self.path, exc_info=True)
            self._json(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            LOGGER.debug("Client disconnected during %s", self.path, exc_info=True)
        except Exception as exc:
            LOGGER.exception("Request failed: %s", self.path)
            self._json(500, {"error": f"操作失敗：{type(exc).__name__}: {exc}"})

    def _check_request_origin(self) -> None:
        hosts = self.headers.get_all("Host", [])
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if len(hosts) != 1 or hosts[0].lower() not in allowed:
            raise RequestError(403, "僅允許透過本機服務網址操作。")
        origins = self.headers.get_all("Origin", [])
        if origins and (len(origins) != 1 or origins[0].lower() != f"http://{hosts[0].lower()}"):
            raise RequestError(403, "不允許其他網站操作本機服務。")

    def _get(self, path: str, query: str) -> None:
        if path == "/api/config":
            self._json(200, self.server.state.config())
        elif match := re.fullmatch(r"/api/downloads/([a-f0-9]{32})", path):
            query_fields = parse_qs(query, keep_blank_values=True)
            cursor = query_fields.get("after", ["0"])
            if len(cursor) != 1 or not re.fullmatch(r"[0-9]{1,12}", cursor[0]):
                raise RequestError(400, "下載事件游標必須為非負整數。")
            self._json(200, self.server.state.job_snapshot(match[1], int(cursor[0])))
        elif path in STATIC_FILES:
            filename, content_type = STATIC_FILES[path]
            content = (Path(__file__).parent / "static" / filename).read_bytes()
            self._send(200, content, f"{content_type}; charset=utf-8")
        else:
            raise RequestError(404, "找不到此頁面或功能。")

    def _post(self, path: str) -> None:
        state = self.server.state
        if path == "/api/workbooks":
            if self.headers.get_content_type() != "application/octet-stream":
                raise RequestError(415, "Excel 上傳需使用 application/octet-stream。")
            filename = unquote(self.headers.get("X-Filename", ""), errors="strict")
            if not filename or len(filename) > 1024:
                raise RequestError(400, "請提供有效的 Excel 檔名。")
            self._json(201, state.add_workbook(filename, self._body(MAX_UPLOAD_BYTES)))
            return
        if path not in {"/api/layout", "/api/import", "/api/cases/validate", "/api/downloads"}:
            raise RequestError(404, "找不到此功能。")
        payload = self._json_body()
        if path == "/api/layout":
            workbook, sheet = state.workbook(payload)
            columns, start_row = detect_case_number_layout(workbook, sheet_name=sheet)
            self._json(200, {"columns": list(columns), "start_row": start_row})
        elif path == "/api/import":
            workbook, sheet = state.workbook(payload)
            columns = payload.get("columns")
            if not isinstance(columns, list) or len(columns) != 4 or not all(
                isinstance(column, str) and re.fullmatch(r"[A-Za-z]{1,3}", column.strip()) for column in columns
            ):
                raise RequestError(400, "請指定法院、年度、字別、號數的四個 Excel 英文字母欄位。")
            start_row = _integer(payload.get("start_row"), "第一筆資料列", positive=True)
            warnings = []
            cases = read_case_numbers(workbook, *(column.strip().upper() for column in columns),
                                      sheet_name=sheet, start_row=start_row, on_error=warnings.append)
            self._json(200, {"cases": _serialize(cases), "warnings": warnings})
        elif path == "/api/cases/validate":
            self._json(200, {"case": _serialize(_case_number(payload, positive=True))})
        else:
            self._json(202, state.start_download(payload))

    def _body(self, limit: int) -> bytes:
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0]):
            raise RequestError(400, "請提供有效的 Content-Length，且不要使用串流傳輸。")
        length = int(lengths[0])
        if length > limit:
            raise RequestError(413, f"檔案或資料過大，上限為 {limit // (1024 * 1024)} MB。")
        if length == 0:
            raise RequestError(400, "請提供檔案或資料內容。")
        content = self.rfile.read(length)
        if len(content) != length:
            raise RequestError(400, "傳送的內容不完整，請重試。")
        return content

    def _json_body(self) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise RequestError(415, "請使用 application/json 傳送資料。")
        try:
            payload = json.loads(self._body(MAX_JSON_BYTES).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestError(400, "無法解析 JSON 資料。") from exc
        if not isinstance(payload, dict):
            raise RequestError(400, "JSON 資料必須是物件。")
        return payload

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send(self, status: int, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
                         "frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(content)


def _integer(value: Any, label: str, *, positive: bool) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise RequestError(400, f"{label}必須是{'正' if positive else '非負'}整數。")
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]+", text):
        raise RequestError(400, f"{label}必須是 ASCII {'正' if positive else '非負'}整數。")
    integer = int(text)
    if positive and integer < 1:
        raise RequestError(400, f"{label}必須是正整數。")
    return integer


def _case_number(payload: Any, *, positive: bool) -> CaseNumber:
    if not isinstance(payload, dict):
        raise RequestError(400, "案號資料必須是物件。")
    court = payload.get("court")
    if not isinstance(court, str) or court not in COURT_MAP:
        raise RequestError(400, "請從搜尋結果選擇有效法院。")
    word = payload.get("case")
    if not isinstance(word, str) or not word.strip():
        raise RequestError(400, "字別不可空白。")
    return CaseNumber(court, _integer(payload.get("year"), "年度", positive=positive), word.strip(),
                      _integer(payload.get("number"), "號數", positive=positive))


def _serialize(value: Any) -> Any:
    if isinstance(value, CaseNumber):
        result = {**value.to_dict(), "court_name": value.court_name}
        if value.original_case is not None:
            result["original_case"] = _serialize(value.original_case)
        return result
    if isinstance(value, JudgmentId):
        return {"case_number": _serialize(value.case_number), "date": value.date,
                "sequence": value.sequence, "category": value.category}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


if __name__ == "__main__":
    main()
