"""主視窗與三個獨立分頁。"""
import sys
import tkinter as tk
from tkinter import ttk, font as tkfont

from .download_page import DownloadPage
from .analysis_page import AnalysisPage
from .compare_page import ComparePage


def main() -> None:
    if sys.platform == "win32":
        import ctypes
        try:
            # 在建立 Tk 前啟用系統 DPI 感知，字型採用系統縮放比例。
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    root = tk.Tk()
    JudgmentApp(root)
    root.mainloop()


class JudgmentApp:
    def __init__(self, root):
        self.root = root
        root.title("裁判書下載、分析與比對")
        scale = root.winfo_fpixels("1i") / 96
        screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
        left, top = 0, 0
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            work_area = wintypes.RECT()
            user32 = ctypes.windll.user32
            # SPI_GETWORKAREA 排除工作列；geometry 指定的是內容區尺寸，
            # 因此還要扣掉標題列及上下視窗邊框。
            if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0):
                left, top = work_area.left, work_area.top
                border_x = user32.GetSystemMetrics(32) + user32.GetSystemMetrics(92)
                border_y = user32.GetSystemMetrics(33) + user32.GetSystemMetrics(92)
                screen_width = max(1, work_area.right - left - 2 * border_x)
                screen_height = max(1, work_area.bottom - top - user32.GetSystemMetrics(4) - 2 * border_y)
        root.geometry(f"{min(round(1000 * scale), screen_width)}x{screen_height}+{left}+{top}")
        root.minsize(min(round(760 * scale), screen_width), min(round(480 * scale), screen_height))
        ttk.Style(root).configure("Treeview", rowheight=tkfont.nametofont("TkDefaultFont").metrics("linespace") + round(8 * scale))

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True)
        self.download_page = DownloadPage(self.notebook)
        self.analysis_page = AnalysisPage(self.notebook)
        self.compare_page = ComparePage(self.notebook)
        for label, page in (("下載判決書", self.download_page),
                            ("分析判決書", self.analysis_page), ("結果比對", self.compare_page)):
            self.notebook.add(page, text=label)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def close(self):
        from tkinter import messagebox
        pages = (self.download_page, self.analysis_page, self.compare_page)
        if any(page.busy for page in pages):
            messagebox.showinfo("工作進行中", "請等待目前工作完成後再關閉，避免中斷檔案儲存。", parent=self.root)
            return
        for page in pages:
            page.stop_polling()
        self.root.destroy()
