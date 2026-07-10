"""Windows GUI launcher for CSV/Excel based 1688 image search."""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import os
from pathlib import Path
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any


def get_app_root() -> Path:
    """Return the working root for source runs and frozen executables."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = get_app_root()
SRC_ROOT = APP_ROOT / "src"

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

os.chdir(APP_ROOT)

from scripts import search_1688_by_csv_main_images as csv_runner


def parse_browser_ids(raw_value: str) -> list[str]:
    """Split browser ids from commas, whitespace, and newlines."""

    return [item for item in re.split(r"[\s,，]+", raw_value.strip()) if item]


class QueueWriter:
    """Forward stdout/stderr content to a UI queue."""

    def __init__(self, log_queue: queue.Queue[str]) -> None:
        self.log_queue = log_queue

    def write(self, value: str) -> int:
        if value:
            self.log_queue.put(value)
        return len(value)

    def flush(self) -> None:
        return


class Csv1688GuiApp:
    """Simple desktop GUI for launching the CSV 1688 image search flow."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("1688 图搜图 CSV/Excel 工具")
        self.root.geometry("920x760")

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker_thread: threading.Thread | None = None

        self.file_path_var = tk.StringVar()
        self.sheet_name_var = tk.StringVar()
        self.max_products_var = tk.StringVar(value="")
        self.max_results_var = tk.StringVar(value="3")
        self.download_dir_var = tk.StringVar(value="data/raw/csv_source_images")
        self.background_var = tk.BooleanVar(value=False)
        self.no_resume_var = tk.BooleanVar(value=False)
        self.workers_var = tk.StringVar(value="1")

        self.browser_ids_text: tk.Text | None = None
        self.log_text: tk.Text | None = None
        self.start_button: ttk.Button | None = None

        self.build_layout()
        self.root.after(150, self.flush_logs)

    def build_layout(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(8, weight=1)

        ttk.Label(frame, text="表格文件").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 10))
        file_row = ttk.Frame(frame)
        file_row.grid(row=0, column=1, sticky="ew", pady=(0, 10))
        file_row.columnconfigure(0, weight=1)
        ttk.Entry(file_row, textvariable=self.file_path_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(file_row, text="选择文件", command=self.choose_file).grid(row=0, column=1, padx=(8, 0))

        ttk.Label(frame, text="Excel Sheet").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(0, 10))
        ttk.Entry(frame, textvariable=self.sheet_name_var).grid(row=1, column=1, sticky="ew", pady=(0, 10))

        ttk.Label(frame, text="BitBrowser IDs").grid(row=2, column=0, sticky="nw", padx=(0, 8), pady=(0, 10))
        browser_ids_frame = ttk.Frame(frame)
        browser_ids_frame.grid(row=2, column=1, sticky="ew", pady=(0, 10))
        browser_ids_frame.columnconfigure(0, weight=1)
        self.browser_ids_text = tk.Text(browser_ids_frame, height=5, wrap="word")
        self.browser_ids_text.grid(row=0, column=0, sticky="ew")
        self.browser_ids_text.bind("<<Modified>>", self.on_browser_ids_changed)
        ttk.Label(
            browser_ids_frame,
            text="支持逗号、空格、换行分隔；输入几个 ID，workers 就等于几个。",
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        numeric_frame = ttk.Frame(frame)
        numeric_frame.grid(row=3, column=1, sticky="ew", pady=(0, 10))
        for column_index in range(3):
            numeric_frame.columnconfigure(column_index, weight=1)

        ttk.Label(frame, text="运行参数").grid(row=3, column=0, sticky="nw", padx=(0, 8), pady=(0, 10))
        ttk.Label(numeric_frame, text="Max Products").grid(row=0, column=0, sticky="w")
        ttk.Entry(numeric_frame, textvariable=self.max_products_var).grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ttk.Label(numeric_frame, text="Max Results").grid(row=0, column=1, sticky="w")
        ttk.Entry(numeric_frame, textvariable=self.max_results_var).grid(row=1, column=1, sticky="ew", padx=(0, 8))
        ttk.Label(numeric_frame, text="Workers").grid(row=0, column=2, sticky="w")
        ttk.Entry(numeric_frame, textvariable=self.workers_var, state="readonly").grid(row=1, column=2, sticky="ew")

        ttk.Label(frame, text="下载目录").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=(0, 10))
        download_row = ttk.Frame(frame)
        download_row.grid(row=4, column=1, sticky="ew", pady=(0, 10))
        download_row.columnconfigure(0, weight=1)
        ttk.Entry(download_row, textvariable=self.download_dir_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(download_row, text="选择目录", command=self.choose_download_dir).grid(row=0, column=1, padx=(8, 0))

        options_row = ttk.Frame(frame)
        options_row.grid(row=5, column=1, sticky="w", pady=(0, 10))
        ttk.Checkbutton(options_row, text="后台运行浏览器", variable=self.background_var).grid(row=0, column=0, padx=(0, 12))
        ttk.Checkbutton(options_row, text="忽略断点续跑", variable=self.no_resume_var).grid(row=0, column=1)

        action_row = ttk.Frame(frame)
        action_row.grid(row=6, column=1, sticky="w", pady=(0, 10))
        self.start_button = ttk.Button(action_row, text="开始执行", command=self.start_run)
        self.start_button.grid(row=0, column=0)
        ttk.Button(action_row, text="清空日志", command=self.clear_log).grid(row=0, column=1, padx=(8, 0))

        ttk.Label(frame, text="运行日志").grid(row=7, column=0, sticky="nw", padx=(0, 8), pady=(0, 8))
        self.log_text = tk.Text(frame, height=22, wrap="word")
        self.log_text.grid(row=8, column=0, columnspan=2, sticky="nsew")

    def choose_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择 CSV 或 Excel 文件",
            filetypes=[
                ("CSV/Excel", "*.csv *.xlsx *.xls"),
                ("CSV", "*.csv"),
                ("Excel", "*.xlsx *.xls"),
                ("All files", "*.*"),
            ],
        )
        if selected:
            self.file_path_var.set(selected)

    def choose_download_dir(self) -> None:
        selected = filedialog.askdirectory(title="选择主图下载目录")
        if selected:
            self.download_dir_var.set(selected)

    def on_browser_ids_changed(self, _event: tk.Event | None = None) -> None:
        if self.browser_ids_text is None:
            return
        raw_value = self.browser_ids_text.get("1.0", tk.END)
        worker_count = max(1, len(parse_browser_ids(raw_value)))
        self.workers_var.set(str(worker_count))
        self.browser_ids_text.edit_modified(False)

    def clear_log(self) -> None:
        if self.log_text is not None:
            self.log_text.delete("1.0", tk.END)

    def append_log(self, message: str) -> None:
        if self.log_text is None:
            return
        self.log_text.insert(tk.END, message)
        self.log_text.see(tk.END)

    def flush_logs(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.append_log(message)
        self.root.after(150, self.flush_logs)

    def set_running(self, running: bool) -> None:
        if self.start_button is not None:
            self.start_button.config(state=tk.DISABLED if running else tk.NORMAL)

    def parse_optional_int(self, raw_value: str, field_name: str) -> int | None:
        text = raw_value.strip()
        if not text:
            return None
        try:
            value = int(text)
        except ValueError as exc:
            raise ValueError(f"{field_name} 必须是整数。") from exc
        if value <= 0:
            raise ValueError(f"{field_name} 必须大于 0。")
        return value

    def start_run(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("正在运行", "当前任务尚未结束。")
            return

        file_path = self.file_path_var.get().strip()
        if not file_path:
            messagebox.showerror("参数错误", "请先选择 CSV 或 Excel 文件。")
            return

        source_path = Path(file_path)
        if not source_path.exists():
            messagebox.showerror("参数错误", f"文件不存在：{source_path}")
            return

        try:
            max_products = self.parse_optional_int(self.max_products_var.get(), "Max Products")
            max_results = self.parse_optional_int(self.max_results_var.get(), "Max Results")
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        browser_ids = parse_browser_ids(self.browser_ids_text.get("1.0", tk.END) if self.browser_ids_text else "")
        workers = len(browser_ids) if browser_ids else None
        browser_ids_value = ",".join(browser_ids)

        suffix = source_path.suffix.lower()
        if suffix not in {".csv", ".xlsx", ".xls"}:
            messagebox.showerror("参数错误", "只支持 CSV / XLS / XLSX 文件。")
            return

        args = csv_runner.build_runtime_args(
            csv_path=str(source_path) if suffix == ".csv" else csv_runner.DEFAULT_CSV_NAME,
            excel_path=str(source_path) if suffix in {".xlsx", ".xls"} else "",
            sheet_name=self.sheet_name_var.get().strip(),
            max_products=max_products,
            max_results=max_results,
            download_dir=self.download_dir_var.get().strip() or "data/raw/csv_source_images",
            background=self.background_var.get(),
            bitbrowser_browser_ids=browser_ids_value,
            workers=workers,
            no_resume=self.no_resume_var.get(),
        )

        self.clear_log()
        self.append_log(f"[gui] app_root: {APP_ROOT}\n")
        self.append_log(f"[gui] file: {source_path}\n")
        self.append_log(f"[gui] workers: {workers or 1}\n")
        self.set_running(True)

        self.worker_thread = threading.Thread(target=self.run_task, args=(args,), daemon=True)
        self.worker_thread.start()

    def run_task(self, args: Any) -> None:
        writer = QueueWriter(self.log_queue)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                summary = csv_runner.run_with_args(args)
            self.log_queue.put(f"[gui] completed. excel={summary.get('excel_path') or '-'}\n")
            self.log_queue.put(f"[gui] completed. json={summary.get('json_path') or '-'}\n")
            self.root.after(0, lambda: messagebox.showinfo("执行完成", "任务已执行完成。"))
        except Exception as exc:
            error_message = str(exc)
            self.log_queue.put(f"[gui] failed: {error_message}\n")
            self.root.after(0, lambda: messagebox.showerror("执行失败", error_message))
        finally:
            self.root.after(0, lambda: self.set_running(False))


def main() -> None:
    """Launch the desktop GUI."""

    mp.freeze_support()
    root = tk.Tk()
    app = Csv1688GuiApp(root)
    app.on_browser_ids_changed()
    root.mainloop()


if __name__ == "__main__":
    main()
