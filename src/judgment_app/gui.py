"""Excel 案號讀取介面：python -m judgment_app.gui。"""

from queue import Empty, Queue
from threading import Thread
import tkinter as tk
import sys
from tkinter import font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pandas as pd

from judgment_app.read_case_numbers import COURT_MAP, read_case_numbers
from judgment_app.read_judgment_web import download_cases
from judgment_app.paths import default_output_dir, resolve_output_path


def main() -> None:
    if sys.platform == "win32":
        import ctypes
        try:
            # 在建立 Tk 前啟用系統 DPI 感知，字型採用系統縮放比例。
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    root = tk.Tk()
    CaseReaderApp(root)
    root.mainloop()


class CaseReaderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("裁判書搜尋與下載")
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
        self.events = Queue()
        self.busy = False
        self.cases = []
        self.sort_column = None
        self.sort_reverse = False
        self.sort_rules = []
        self.sort_description = tk.StringVar(value="尚未設定排序")
        self.file = tk.StringVar()
        self.sheet = tk.StringVar()
        self.start_row = tk.StringVar(value="4")
        self.columns = [tk.StringVar(value=value) for value in ("AL", "AM", "AN", "AO")]
        self.status = tk.StringVar(value="請先選擇 Excel 檔案。")
        self.controls = []
        self.output = tk.StringVar(value=str(default_output_dir()))
        self.web = tk.BooleanVar(value=True)
        self.pdf = tk.BooleanVar(value=False)
        self.api = tk.BooleanVar(value=False)

        frame = ttk.Frame(root, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(5, weight=1)
        frame.rowconfigure(7, weight=1)
        ttk.Label(frame, text="Excel 案號讀取", font=("Microsoft JhengHei", 14, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )
        ttk.Label(frame, text="Excel 檔案").grid(row=1, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.file, state="readonly").grid(
            row=1, column=1, sticky="ew", padx=10
        )
        browse = ttk.Button(frame, text="瀏覽…", command=self.choose_file)
        browse.grid(row=1, column=2)
        self.controls.append((browse, "normal"))

        options = ttk.LabelFrame(frame, text="讀取設定", padding=12)
        options.grid(row=2, column=0, columnspan=3, sticky="ew", pady=16)
        options.columnconfigure(0, weight=1)
        fields = ttk.Frame(options)
        fields.grid(row=0, column=0, sticky="ew")
        for index in range(4):
            fields.columnconfigure(index * 2, uniform="setting_labels")
            fields.columnconfigure(index * 2 + 1, weight=1, uniform="setting_inputs")
        ttk.Label(fields, text="工作表").grid(row=0, column=0, sticky="w")
        self.sheet_box = ttk.Combobox(fields, textvariable=self.sheet, state="readonly", width=24)
        self.sheet_box.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(8, 20))
        self.controls.append((self.sheet_box, "readonly"))
        ttk.Label(fields, text="第一筆資料列").grid(row=0, column=4, sticky="w")
        row_box = ttk.Entry(fields, textvariable=self.start_row, width=8)
        row_box.grid(row=0, column=5, sticky="ew", padx=(8, 20))
        self.controls.append((row_box, "normal"))
        for index, (label, variable) in enumerate(zip(("法院", "年度", "字別", "號數"), self.columns)):
            ttk.Label(fields, text=label).grid(row=1, column=index * 2, sticky="w", pady=(14, 0))
            entry = ttk.Entry(fields, textvariable=variable, width=8)
            entry.grid(row=1, column=index * 2 + 1, sticky="ew", padx=(8, 20), pady=(14, 0))
            self.controls.append((entry, "normal"))
        ttk.Label(options, text="欄位請填 Excel 英文字母（例如 AL）；資料列從 1 起算，不包含標題列。",
                  wraplength=680).grid(row=2, column=0, columnspan=8, sticky="w", pady=(12, 0))

        self.read_button = ttk.Button(frame, text="讀取案號", command=self.read_cases, state="disabled")
        self.read_button.grid(row=3, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.status, wraplength=680).grid(
            row=3, column=1, columnspan=2, sticky="w", padx=10
        )
        manual = ttk.LabelFrame(frame, text="案號清單：Excel 匯入會合併去重；點選欄位標題排序", padding=8)
        manual.grid(row=4, column=0, columnspan=3, sticky="ew", pady=8)
        for column in (1, 3, 5, 7):
            manual.columnconfigure(column, weight=1)
        self.manual_courts = {f"{name} ({code})": code for code, name in COURT_MAP.items() if code}
        self.manual_court = tk.StringVar(value=next(label for label, code in self.manual_courts.items() if code == "TPD"))
        self.manual_year = tk.StringVar()
        self.manual_case = tk.StringVar()
        self.manual_number = tk.StringVar()
        court_box = ttk.Combobox(manual, textvariable=self.manual_court, values=list(self.manual_courts), state="readonly", width=30)
        ttk.Label(manual, text="法院").grid(row=0, column=0)
        court_box.grid(row=0, column=1, padx=4, sticky="ew")
        self.controls.append((court_box, "readonly"))
        for index, (label, variable) in enumerate((("年度", self.manual_year), ("字別", self.manual_case), ("號數", self.manual_number))):
            ttk.Label(manual, text=label).grid(row=0, column=2 + index * 2)
            entry = ttk.Entry(manual, textvariable=variable, width=8)
            entry.grid(row=0, column=3 + index * 2, padx=4, sticky="ew")
            self.controls.append((entry, "normal"))
        actions = ttk.Frame(manual)
        actions.grid(row=1, column=0, columnspan=8, sticky="w", pady=(8, 0))
        for index, (label, command) in enumerate((("新增單筆", self.add_case), ("刪除選取案號", self.delete_case), ("多欄位排序…", self.configure_sort))):
            button = ttk.Button(actions, text=label, command=command)
            button.grid(row=0, column=index, sticky="w", padx=(0, 8))
            self.controls.append((button, "normal"))
        ttk.Label(manual, text="Ctrl／Shift 可多選；刪除僅移除清單案號。").grid(row=2, column=0, columnspan=8, sticky="w")
        ttk.Label(manual, textvariable=self.sort_description, wraplength=850).grid(row=3, column=0, columnspan=8, sticky="w")
        table = ttk.Frame(frame)
        table.grid(row=5, column=0, columnspan=3, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, height=1, columns=("index", "court", "code", "year", "case", "number"), show="headings", selectmode="extended")
        for key, label, width in zip(self.tree["columns"], ("序號", "法院", "法院代碼", "年度", "字別", "號數"), (60, 300, 90, 70, 100, 100)):
            self.tree.heading(key, text=label, command=lambda column=key: self.sort_cases(column))
            self.tree.column(key, width=round(width * scale), minwidth=round(50 * scale), anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        downloads = ttk.LabelFrame(frame, text="下載設定（可複選；依目前搜尋條件取得刑事判決）", padding=10)
        downloads.grid(row=6, column=0, columnspan=3, sticky="ew", pady=10)
        downloads.columnconfigure(1, weight=1)
        for index, (label, variable) in enumerate((
            ("透過網頁搜尋判決書（全文 TXT）", self.web),
            ("透過網頁下載判決書 PDF", self.pdf),
            ("透過 JID 和 API 得到判決書（JSON）", self.api),
        )):
            control = ttk.Checkbutton(downloads, text=label, variable=variable)
            control.grid(row=index, column=0, columnspan=3, sticky="w")
            self.controls.append((control, "normal"))
        ttk.Label(downloads, text="儲存路徑").grid(row=3, column=0, sticky="w", pady=8)
        destination = ttk.Entry(downloads, textvariable=self.output)
        destination.grid(row=3, column=1, sticky="ew", padx=8)
        choose = ttk.Button(downloads, text="選擇資料夾…", command=self.choose_output)
        choose.grid(row=3, column=2)
        self.controls.extend(((destination, "normal"), (choose, "normal")))
        self.download_button = ttk.Button(downloads, text="依畫面順序下載全部案號", command=self.start_download, state="disabled")
        self.download_button.grid(row=4, column=0, columnspan=3, sticky="w")
        log_frame = ttk.Frame(frame)
        log_frame.grid(row=7, column=0, columnspan=3, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=1, width=1, font="TkDefaultFont", wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=log_scroll.set)
        # 先預留 Treeview 標題列，再由兩個結果區平分剩餘高度。
        # 關閉尺寸傳播，避免 Text 與 Treeview 的預設需求高度影響比例。
        table.grid_propagate(False)
        log_frame.grid_propagate(False)

        def size_result_areas():
            row_height = int(ttk.Style(root).lookup("Treeview", "rowheight"))
            heading_height = max(0, self.tree.winfo_reqheight() - row_height)
            frame.rowconfigure(5, minsize=heading_height + 1)
            frame.rowconfigure(7, minsize=1)

        root.after_idle(size_result_areas)
        for variable in (self.sheet, self.start_row, *self.columns):
            variable.trace_add("write", self.settings_changed)
        root.after(100, self.poll)

    def choose_file(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="選擇 Excel 檔案",
                                          filetypes=[("Excel 檔案", "*.xls *.xlsx")])
        if not path:
            return
        self.file.set(path)
        self.sheet.set("")
        self.sheet_box.configure(values=())
        self.status.set("正在讀取工作表…")

        def sheets():
            with pd.ExcelFile(path) as workbook:
                return workbook.sheet_names

        self.run_task("sheets", sheets)

    def read_cases(self) -> None:
        try:
            start = int(self.start_row.get())
            if start < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("設定錯誤", "第一筆資料列必須是大於 0 的整數。", parent=self.root)
            return
        path, sheet = self.file.get(), self.sheet.get()
        columns = [variable.get().strip().upper() for variable in self.columns]
        self.status.set("正在讀取與檢查案號…")
        self.run_task("cases", lambda: read_case_numbers(path, *columns, sheet_name=sheet, start_row=start))

    def choose_output(self):
        path = filedialog.askdirectory(parent=self.root, title="選擇判決書儲存資料夾")
        if path:
            self.output.set(path)

    def start_download(self):
        if not self.cases or self.busy:
            return
        web, pdf, api = self.web.get(), self.pdf.get(), self.api.get()
        if not any((web, pdf, api)) or not self.output.get().strip():
            messagebox.showerror("下載設定", "請選擇至少一個項目，並指定儲存路徑。", parent=self.root)
            return
        cases = self.visible_cases()
        output = resolve_output_path(self.output.get().strip())
        self.status.set("正在搜尋與下載…")
        self.run_task("download", lambda: download_cases(
            cases, output, web=web, pdf=pdf, api=api,
            progress=lambda text: self.events.put(("progress", text, None)),
            on_result=lambda text: self.events.put(("result", text, None)),
        ))

    def add_case(self):
        if self.busy:
            return
        year, number = self.manual_year.get().strip(), self.manual_number.get().strip()
        word = self.manual_case.get().strip()
        court = self.manual_courts.get(self.manual_court.get())
        if not court or not word or not year.isascii() or not year.isdecimal() or not number.isascii() or not number.isdecimal() or int(year) < 1 or int(number) < 1:
            messagebox.showerror("案號格式", "請選擇法院、填寫字別，年度與號數須為正整數。", parent=self.root)
            return
        case = dict(court=court, year=int(year), case=word, number=int(number))
        if case in self.cases:
            self.status.set("此案號已在清單中，未重複新增。")
            return
        self.cases.append(case)
        self.refresh_cases()

    def delete_case(self):
        if self.busy:
            return
        selected = self.tree.selection()
        if not selected:
            self.status.set("請先選取要刪除的案號。")
            return
        self.tree.delete(*selected)
        self.cases = self.visible_cases()
        self.refresh_cases()

    def sort_cases(self, column):
        if self.busy or column == "index":
            return
        self.sort_reverse = not self.sort_reverse if self.sort_column == column else False
        self.sort_column = column
        self.sort_rules = [(column, self.sort_reverse)]
        self.refresh_cases()

    def configure_sort(self):
        if self.busy:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("多欄位排序")
        dialog.transient(self.root)
        dialog.grab_set()
        labels = {"法院": "court", "法院代碼": "code", "年度": "year", "字別": "case", "號數": "number"}
        reverse_labels = {value: key for key, value in labels.items()}
        ttk.Label(dialog, text="由上到下決定優先順序；前一欄相同時才比較下一欄。").grid(row=0, column=0, columnspan=3, padx=12, pady=12)
        rows = []
        for index in range(5):
            rule = self.sort_rules[index] if index < len(self.sort_rules) else None
            field = tk.StringVar(value=reverse_labels[rule[0]] if rule else "不使用")
            direction = tk.StringVar(value="降冪" if rule and rule[1] else "升冪")
            ttk.Label(dialog, text=f"第 {index + 1} 優先").grid(row=index + 1, column=0, padx=12, pady=4)
            ttk.Combobox(dialog, textvariable=field, values=["不使用", *labels], state="readonly", width=16).grid(row=index + 1, column=1, padx=8)
            ttk.Combobox(dialog, textvariable=direction, values=["升冪", "降冪"], state="readonly", width=8).grid(row=index + 1, column=2, padx=12)
            rows.append((field, direction))

        def apply():
            rules = [(labels[field.get()], direction.get() == "降冪") for field, direction in rows if field.get() != "不使用"]
            if len({column for column, _ in rules}) != len(rules):
                messagebox.showerror("排序設定", "同一欄位只能設定一次。", parent=dialog)
                return
            self.sort_rules = rules
            self.sort_column = None
            self.sort_reverse = False
            dialog.destroy()
            self.refresh_cases()

        ttk.Button(dialog, text="套用", command=apply).grid(row=6, column=1, pady=12)
        ttk.Button(dialog, text="取消", command=dialog.destroy).grid(row=6, column=2, pady=12)

    def merge_cases(self, cases):
        for case in cases:
            if case not in self.cases:
                self.cases.append(dict(case))
        self.refresh_cases()

    def visible_cases(self):
        """以畫面列順序建立下載快照。"""
        cases = []
        for item in self.tree.get_children():
            _, _, court, year, case, number = self.tree.item(item, "values")
            cases.append(dict(court=court, year=int(year), case=case, number=int(number)))
        return cases

    def refresh_cases(self):
        # Python 排序是穩定排序：從最低優先欄位往前套用。
        for column, reverse in reversed(self.sort_rules):
            key = (lambda case: COURT_MAP.get(case["court"], case["court"])) if column == "court" else (lambda case: case["court" if column == "code" else column])
            self.cases.sort(key=key, reverse=reverse)
        labels = {"court": "法院", "code": "法院代碼", "year": "年度", "case": "字別", "number": "號數"}
        self.sort_description.set("排序：" + " → ".join(f"{labels[column]}{'降冪' if reverse else '升冪'}" for column, reverse in self.sort_rules) if self.sort_rules else "未設定排序，保留目前順序")
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.set_busy(True)
        self.render_cases(0)

    def clear_results(self) -> None:
        self.cases = []
        self.download_button.configure(state="disabled")
        for item in self.tree.get_children():
            self.tree.delete(item)

    def settings_changed(self, *_args) -> None:
        if not self.busy and self.file.get():
            self.status.set("匯入設定已變更；現有清單保留，按「讀取案號」合併匯入。")

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        for widget, state in self.controls:
            widget.configure(state="disabled" if busy else state)
        self.read_button.configure(state="normal" if not busy and self.sheet.get() else "disabled")
        self.download_button.configure(state="normal" if not busy and self.cases else "disabled")

    def run_task(self, kind, operation) -> None:
        # 檔案與網路工作在背景執行；所有 Tk 元件由主執行緒更新。
        self.set_busy(True)

        def worker():
            try:
                self.events.put((kind, operation(), None))
            except Exception as exc:
                self.events.put((kind, None, exc))

        Thread(target=worker, daemon=True).start()

    def poll(self) -> None:
        try:
            kind, result, error = self.events.get_nowait()
        except Empty:
            pass
        else:
            if kind in {"progress", "result"}:
                if kind == "progress":
                    self.status.set(result)
                else:
                    self.append_log(result)
                self.root.after(100, self.poll)
                return
            if error is not None:
                self.status.set("作業中止；已儲存的檔案仍保留。")
                self.append_log(str(error))
                messagebox.showerror("作業失敗", str(error), parent=self.root)
            elif kind == "download":
                summary = f"下載完成：{len(result['files'])} 個檔案；{len(result['skipped'])} 個已存在並跳過；{len(result['empty'])} 個案號無符合結果；{len(result['errors'])} 項失敗。"
                self.status.set(summary)
                self.append_log(summary)
                for title, cases in (("無符合結果或未公開的案號", result["empty"]),
                                     ("下載失敗的案號", result.get("failed_cases", []))):
                    self.append_log(f"\n【{title}】")
                    if not cases:
                        self.append_log("無")
                    for case in cases:
                        court = COURT_MAP.get(case["court"], case["court"])
                        self.append_log(f"{court} {case['year']}年度{case['case']}字第{case['number']}號")
                if result["errors"]:
                    self.append_log("\n【失敗原因】")
                    for detail in result["errors"]:
                        self.append_log(detail)
            elif kind == "sheets":
                self.sheet_box.configure(values=result)
                self.sheet.set(result[0] if result else "")
                self.status.set("請確認欄位與起始列，再按「讀取案號」。" if result else "檔案沒有可讀取的工作表。")
            else:
                self.merge_cases(result)
            if kind != "cases" or error is not None:
                self.set_busy(False)
        self.root.after(100, self.poll)

    def render_cases(self, start: int) -> None:
        end = min(start + 200, len(self.cases))
        for index in range(start, end):
            case = self.cases[index]
            self.tree.insert("", "end", values=(index + 1, COURT_MAP.get(case["court"], case["court"]),
                             case["court"], case["year"], case["case"], case["number"]))
        if end < len(self.cases):
            self.status.set(f"正在顯示結果：{end} / {len(self.cases)}")
            self.root.after(1, self.render_cases, end)
        else:
            self.status.set(f"讀取完成，共 {len(self.cases)} 筆不重複案號。" if self.cases else "沒有讀到案號，請確認工作表與起始列。")
            self.set_busy(False)

    def append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


if __name__ == "__main__":
    main()
