"""共用背景工作、路徑選擇與作業紀錄；Tk 更新僅在主執行緒進行。"""
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from judgment_app.paths import resolve_output_path


class TaskPage(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.root = self.winfo_toplevel()
        self.events = Queue()
        self.busy = False
        self.controls = []
        self.status = tk.StringVar(master=self, value="請選擇來源及輸出位置。")
        self._poll_id = None
        self.columnconfigure(0, weight=1)

    def path_field(self, parent, row, label, variable, *, filetypes=None, save=False, directory=False,
                   button_width=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=6)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", padx=6)
        self.controls.append((entry, "normal"))
        actions = ttk.Frame(parent)
        actions.grid(row=row, column=2, sticky="w")
        choices = [("選擇資料夾", "directory")] if directory else (
            [("儲存為…", "save")] if save else [("選擇檔案", "file"), ("選擇資料夾", "directory")])
        for text, mode in choices:
            button = ttk.Button(actions, text=text, command=lambda mode=mode: self.choose_path(variable, mode, filetypes))
            if button_width is not None:
                button.configure(width=button_width)
            button.pack(side="left", padx=3)
            self.controls.append((button, "normal"))

    def choose_path(self, variable, mode, filetypes=None):
        options = {"parent": self.root}
        if mode == "directory":
            path = filedialog.askdirectory(**options)
        elif mode == "save":
            path = filedialog.asksaveasfilename(**options, defaultextension=".json", filetypes=[("JSON", "*.json")])
        else:
            path = filedialog.askopenfilename(**options, filetypes=filetypes or [("所有檔案", "*")])
        if path:
            variable.set(path)

    def required_path(self, variable, label):
        value = variable.get().strip()
        if not value:
            raise ValueError(f"請指定{label}。")
        return resolve_output_path(value)

    def add_log(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self.log = tk.Text(parent, height=6, width=1, wrap="word", state="disabled", font="TkDefaultFont")
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(parent, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

    def set_busy(self, busy):
        self.busy = busy
        for widget, state in self.controls:
            widget.configure(state="disabled" if busy else state)
        if hasattr(self, "progress_bar"):
            self.progress_bar.start(15) if busy else self.progress_bar.stop()

    def run_task(self, kind, operation):
        if self.busy:
            return
        self.set_busy(True)

        def worker():
            try:
                self.events.put((kind, operation(), None))
            except Exception as exc:
                self.events.put((kind, None, exc))

        Thread(target=worker, daemon=True).start()

    def progress(self, text):
        self.events.put(("progress", text, None))

    def on_result(self, text):
        self.events.put(("result", text, None))

    def poll(self):
        # 限制單次批量，避免大量進度訊息阻塞 Tk 事件迴圈。
        for _ in range(50):
            try:
                kind, result, error = self.events.get_nowait()
            except Empty:
                break
            if kind == "progress":
                self.status.set(result)
            elif kind == "result":
                self.append_log(result)
            else:
                deferred = False
                try:
                    if error is not None:
                        self.status.set("作業中止；已儲存的檔案仍保留。")
                        detail = f"{type(error).__name__}: {error}"
                        self.append_log(detail)
                        messagebox.showerror("作業失敗", detail, parent=self.root)
                    else:
                        deferred = self.task_completed(kind, result)
                finally:
                    if not deferred:
                        self.set_busy(False)
        self.schedule_poll()

    def schedule_poll(self):
        self.stop_polling()
        self._poll_id = self.root.after(100, self.poll)

    def stop_polling(self):
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
            self._poll_id = None

    def append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
