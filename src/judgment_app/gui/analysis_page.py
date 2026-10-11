"""獨立選擇既有判決書來源並分析。"""
import tkinter as tk
from tkinter import messagebox, ttk

from judgment_app.analysis.analyzer import analyze_source
from judgment_app.paths import default_output_dir
from .common import TaskPage


class AnalysisPage(TaskPage):
    def __init__(self, parent):
        super().__init__(parent)
        self.source = tk.StringVar(master=self, value=str(default_output_dir()))
        self.output = tk.StringVar(master=self, value=str(default_output_dir().parent / "analyses"))
        self.analysis_provider = tk.StringVar(master=self, value="openai")
        settings = ttk.LabelFrame(self, text="分析來源與輸出", padding=12)
        settings.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        settings.columnconfigure(1, weight=1)
        self.path_field(settings, 0, "判決書 TXT／JSON", self.source, filetypes=[("判決書", "*.txt *.json")])
        self.path_field(settings, 1, "分析結果資料夾", self.output, directory=True)
        ttk.Label(settings, text="AI 服務").grid(row=2, column=0, padx=6)
        provider = ttk.Combobox(settings, textvariable=self.analysis_provider, values=("openai", "gemini"), state="readonly")
        provider.grid(row=2, column=1, sticky="w", padx=6)
        start = ttk.Button(settings, text="開始分析", command=self.start_analysis)
        start.grid(row=2, column=2, sticky="w", padx=6)
        self.controls.extend(((provider, "readonly"), (start, "normal")))
        ttk.Label(settings, text="資料夾沿用既有案號／歷審處理規則；結果保留來源相對目錄，檔名加 .analysis.json。",
                  wraplength=850).grid(row=3, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Label(self, textvariable=self.status, wraplength=1100).grid(row=1, column=0, sticky="w", padx=12)
        self.progress_bar = ttk.Progressbar(self, mode="indeterminate")
        self.progress_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=8)
        log = ttk.Frame(self, padding=12)
        log.grid(row=3, column=0, sticky="nsew")
        self.rowconfigure(3, weight=1)
        self.add_log(log)
        self.schedule_poll()

    def start_analysis(self):
        if self.busy:
            return
        try:
            source = self.required_path(self.source, "分析來源")
            output = self.required_path(self.output, "分析結果資料夾")
            if not source.exists() or (source.is_file() and source.suffix.lower() not in {".txt", ".json"}):
                raise ValueError("請選擇有效的 TXT／JSON 檔案或資料夾。")
            if output.exists() and not output.is_dir():
                raise ValueError("分析結果位置必須為資料夾。")
        except ValueError as exc:
            messagebox.showerror("分析設定", str(exc), parent=self.root)
            return
        provider = self.analysis_provider.get()
        self.status.set("正在準備分析…")
        self.run_task("analysis", lambda: analyze_source(source, output, provider=provider,
                                                        progress=self.progress, on_result=self.on_result))

    def task_completed(self, kind, report):
        summary = f"分析完成：成功 {len(report['files'])} 份，失敗 {len(report['errors'])} 份，共 {report['total']} 份。"
        self.status.set(summary)
        self.append_log(summary)
        for output in report["outputs"]:
            self.append_log(f"JSON：{output}")
        for error in report["errors"]:
            self.append_log(error)
