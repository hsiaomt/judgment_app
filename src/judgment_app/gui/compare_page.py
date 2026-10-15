"""獨立 Excel／JSON 比對與差異表格。"""
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from judgment_app.analysis.compare import compare_to_report, load_comparison_report
from judgment_app.paths import default_output_dir, resolve_output_path
from .common import TaskPage


class ComparePage(TaskPage):
    def __init__(self, parent):
        super().__init__(parent)
        self.comparison_file = tk.StringVar(master=self)
        self.json_source = tk.StringVar(master=self, value=str(default_output_dir().parent / "analyses"))
        self.output = tk.StringVar(master=self, value=str(default_output_dir().parent / "comparison_report.json"))
        settings = ttk.LabelFrame(self, text="比對來源與報告", padding=12)
        settings.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        settings.columnconfigure(1, weight=1)
        self.path_field(settings, 0, "Excel 檔案／資料夾", self.comparison_file, filetypes=[("Excel", "*.xls *.xlsx")], button_width=8)
        self.path_field(settings, 1, "分析 JSON 檔案／資料夾", self.json_source, filetypes=[("JSON", "*.json")], button_width=8)
        self.path_field(settings, 2, "比對報告位置", self.output, save=True, button_width=8)
        start = ttk.Button(settings, text="開始比對", width=-8, command=self.start_comparison)
        start.grid(row=3, column=2, sticky="ew", padx=3, pady=6)
        self.controls.append((start, "normal"))
        import_report = ttk.Button(settings, text="匯入比對報告 JSON", width=24, command=self.import_report)
        import_report.grid(row=3, column=1, sticky="w", padx=6, pady=6)
        self.controls.append((import_report, "normal"))
        ttk.Label(self, textvariable=self.status, wraplength=1100).grid(row=1, column=0, sticky="w", padx=12)
        self.progress_bar = ttk.Progressbar(self, mode="indeterminate")
        self.progress_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=8)
        self.result_tabs = ttk.Notebook(self)
        self.result_tabs.grid(row=3, column=0, sticky="nsew", padx=12, pady=12)
        self.rowconfigure(3, weight=1)
        self.comparison_frame = ttk.Frame(self.result_tabs)
        self.result_tabs.add(self.comparison_frame, text="比對結果（非 equal；雙擊查看完整明細）")
        log = ttk.Frame(self.result_tabs)
        self.result_tabs.add(log, text="作業紀錄")
        self.add_log(log)
        self.comparison_frame.columnconfigure(0, weight=1)
        self.comparison_frame.rowconfigure(0, weight=1)
        columns = ("excel", "sheet", "row", "case_number", "name", "status", "details", "json")
        self.comparison_tree = ttk.Treeview(self.comparison_frame, columns=columns, show="headings")
        for key, label, width in zip(columns,
                ("Excel 檔案", "工作表", "列號", "案號", "姓名", "狀態", "差異／原因", "JSON 檔案"),
                (180, 100, 60, 280, 100, 100, 460, 220)):
            self.comparison_tree.heading(key, text=label)
            # 保留使用者調整後的欄寬，避免自動伸縮抵銷水平捲動範圍。
            self.comparison_tree.column(key, width=width, minwidth=50, anchor="w", stretch=False)
        self.comparison_tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(self.comparison_frame, orient="vertical", command=self.comparison_tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(self.comparison_frame, orient="horizontal", command=self.comparison_tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.comparison_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.comparison_rows = {}
        self.comparison_tree.bind("<Double-1>", self.show_comparison_details)
        self.comparison_tree.bind("<Return>", self.show_comparison_details)
        self.schedule_poll()

    def start_comparison(self):
        if self.busy:
            return
        try:
            excel = self.required_path(self.comparison_file, "Excel 來源")
            source = self.required_path(self.json_source, "分析 JSON 來源")
            destination = self.required_path(self.output, "比對報告位置")
            for path, suffixes in ((excel, {".xls", ".xlsx"}), (source, {".json"})):
                if not path.exists() or (path.is_file() and path.suffix.lower() not in suffixes):
                    raise ValueError(f"來源路徑或檔案格式無效：{path}")
            if destination.is_dir() or destination.suffix.lower() != ".json":
                raise ValueError("請指定 .json 報告檔案。")
        except ValueError as exc:
            messagebox.showerror("比對設定", str(exc), parent=self.root)
            return
        self.status.set("正在準備比對…")
        self.run_task("comparison", lambda: (compare_to_report(excel, source, destination, progress=self.progress), destination))

    def import_report(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self.root, title="匯入現有比對報告",
                                          filetypes=[("比對報告 JSON", "*.json")])
        if not path:
            return
        source = resolve_output_path(path)
        self.status.set("正在匯入比對報告…")
        self.run_task("import_report", lambda: (load_comparison_report(source), source))

    def task_completed(self, kind, result):
        report, destination = result
        rows = [row for row in report["rows"] if row["status"] != "equal"]
        action = "報告匯入完成" if kind == "import_report" else "比對完成"
        summary = (f"{action}：共 {report['summary']['total']} 列，equal {report['summary'].get('equal', 0)} 列，"
                   f"非 equal {len(rows)} 列，讀取錯誤 {len(report['errors'])} 項。")
        self.status.set(summary)
        self.append_log(summary)
        self.append_log(f"比對報告：{destination}")
        for error in report["errors"]:
            self.append_log(json.dumps(error, ensure_ascii=False))
        self.render_comparison(rows, report["errors"])
        return True

    def render_comparison(self, rows, errors):
        for item in self.comparison_tree.get_children():
            self.comparison_tree.delete(item)
        self.comparison_rows.clear()
        self.result_tabs.select(self.comparison_frame)
        # 分批插入，讓大型報告不會長時間占用 Tk 主執行緒。
        records = iter([(row, False) for row in rows] + [(error, True) for error in errors])
        self.set_busy(True)

        def insert_batch():
            for _ in range(200):
                try:
                    row, is_error = next(records)
                except StopIteration:
                    self.set_busy(False)
                    return
                details = [row["reason"]] if row.get("reason") else []
                for diff in row.get("differences", []):
                    details.append(f"{diff['column']}：Excel={diff.get('excel_value')}，JSON={diff.get('json_value')}")
                for field in row.get("uncomparable", []):
                    details.append(f"{field['column']}：{field['reason']}")
                item = self.comparison_tree.insert("", "end", values=(
                    row.get("file" if is_error else "excel", ""), row.get("sheet", ""), row.get("row", ""),
                    row.get("case_number", ""), row.get("name", ""), "error" if is_error else row["status"],
                    row["error"] if is_error else "；".join(details), row.get("json", "")))
                self.comparison_rows[item] = row
            self.root.after(1, insert_batch)

        insert_batch()

    def show_comparison_details(self, event=None):
        item = (self.comparison_tree.identify_row(event.y)
                if event is not None and event.keysym != "Return" else self.comparison_tree.focus())
        if item not in self.comparison_rows:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("比對結果明細")
        dialog.geometry(f"{min(1000, self.root.winfo_screenwidth())}x{min(650, self.root.winfo_screenheight())}")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)
        detail = tk.Text(dialog, wrap="word", font="TkDefaultFont")
        detail.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(dialog, orient="vertical", command=detail.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        detail.configure(yscrollcommand=scroll.set)
        detail.insert("1.0", json.dumps(self.comparison_rows[item], ensure_ascii=False, indent=2))
        detail.configure(state="disabled")
