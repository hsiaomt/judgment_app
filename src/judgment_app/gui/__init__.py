"""桌面介面；保留 main 與原下載介面名稱供既有入口匯入。"""
from .app import JudgmentApp, main
from .download_page import DownloadPage
from judgment_app.acquisition.read_case_numbers import read_case_numbers

CaseReaderApp = DownloadPage

__all__ = ["main", "JudgmentApp", "DownloadPage", "CaseReaderApp", "read_case_numbers"]
