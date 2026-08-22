#!/usr/bin/env python3
"""
CoProducer Core Analyzer - LEGACY Tkinter GUI (Internal Testing Only)

This file is kept for quick internal testing.
Do NOT use for product. The production desktop UI is CoProducerDesktop.py (PySide6).
"""

import sys
import threading
import queue
import logging
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, scrolledtext

# Ensure we can import the engine from source or installed package
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))

try:
    from nodaw.core.engine import WorkflowRunner
    from nodaw import __version__ as VERSION, APP_NAME
except ImportError:
    print("ERROR: Could not import nodaw engine. Run from project root or install with 'pip install -e .'")
    sys.exit(1)

MODES = {
    "1": ("analyze", "Single-file analysis"),
    "2": ("reference", "Reference comparison"),
    "3": ("batch", "Folder / batch analysis"),
    "4": ("album", "Album consistency analysis"),
    "5": ("codecs", "Codec analysis and previews"),
    "6": ("streaming", "Streaming readiness and previews"),
    "7": ("fixes", "Repair recommendations"),
    "8": ("history", "Project history dashboard"),
    "9": ("all", "Complete analysis (all modes)"),
    "10": ("export", "Export current reports"),
    "11": ("doctor", "Dependency diagnostics"),
}

class CoProducerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{VERSION} - Desktop")
        self.geometry("980x720")
        self.minsize(800, 600)

        self.queue = queue.Queue()
        self.current_runner = None
        self.last_report_dir = None

        # State
        self.no_previews_var = tk.BooleanVar(value=False)
        self.verbose_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._poll_queue()

        # Initial doctor hint
        self.log("CoProducer Desktop ready. Use the buttons or menu to run analyses.\n")

    def _build_ui(self):
        # Top bar
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text=f"{APP_NAME} v{VERSION}", font=("Segoe UI", 14, "bold")).pack(side="left")
        ttk.Label(top, text="   |   Presentation layer over stable engine", foreground="#666").pack(side="left")

        # Options
        opts = ttk.Frame(top)
        opts.pack(side="right")
        ttk.Checkbutton(opts, text="No previews", variable=self.no_previews_var).pack(side="left", padx=4)
        ttk.Checkbutton(opts, text="Verbose log", variable=self.verbose_var).pack(side="left", padx=4)
        ttk.Button(opts, text="Run Doctor", command=lambda: self.run_mode("doctor")).pack(side="left", padx=8)

        # Main content: left modes, right log+controls
        main = ttk.PanedWindow(self, orient="horizontal")
        main.pack(fill="both", expand=True, padx=8, pady=8)

        # Left: modes
        left = ttk.Frame(main, width=280)
        main.add(left, weight=0)

        ttk.Label(left, text="Analysis Modes", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 6))

        for key, (mode, label) in MODES.items():
            btn = ttk.Button(left, text=f"{key}. {label}", 
                             command=lambda m=mode: self.run_mode(m))
            btn.pack(fill="x", pady=2)

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=10)
        ttk.Button(left, text="Open last report folder", command=self.open_last_report).pack(fill="x", pady=2)
        ttk.Button(left, text="Open latest HTML report", command=self.open_latest_html).pack(fill="x", pady=2)

        # Right: log and status
        right = ttk.Frame(main)
        main.add(right, weight=1)

        ttk.Label(right, text="Log / Status", font=("Segoe UI", 11, "bold")).pack(anchor="w")

        self.log_text = scrolledtext.ScrolledText(right, height=20, wrap="word", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, pady=4)

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status = ttk.Label(right, textvariable=self.status_var, relief="sunken", padding=4)
        status.pack(fill="x")

        # Bottom buttons
        bottom = ttk.Frame(right)
        bottom.pack(fill="x", pady=4)
        ttk.Button(bottom, text="Clear Log", command=self.clear_log).pack(side="left")
        ttk.Button(bottom, text="Exit", command=self.destroy).pack(side="right")

    def log(self, msg: str):
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.update_idletasks()

    def clear_log(self):
        self.log_text.delete("1.0", "end")

    def set_status(self, text: str):
        self.status_var.set(text)
        self.update_idletasks()

    def _poll_queue(self):
        try:
            while True:
                item = self.queue.get_nowait()
                if item[0] == "log":
                    self.log(item[1])
                elif item[0] == "status":
                    self.set_status(item[1])
                elif item[0] == "done":
                    self.set_status("Analysis complete")
                    self.log("\n=== Analysis finished successfully ===")
                    report = item[1]
                    mode = item[2]
                    self.last_report_dir = ROOT / "reports"
                    self.log(f"Report type: {report.get('report_type')}")
                    if report.get('score') is not None:
                        self.log(f"Score: {report['score']} / 100")
                    self.log("Use buttons on the left to open reports.")
                elif item[0] == "error":
                    self.set_status("Error")
                    self.log(f"\n[ERROR] {item[1]}")
                    messagebox.showerror("Analysis Error", str(item[1]))
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def run_mode(self, mode: str):
        self.set_status(f"Running {mode}...")
        self.log(f"\n>>> Starting mode: {mode}")

        song = None
        ref = None
        folder = None

        # Collect inputs via dialogs based on mode
        if mode in ("analyze", "reference", "codecs", "streaming", "fixes"):
            song = filedialog.askopenfilename(
                title="Select primary audio file",
                filetypes=[("Audio files", "*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.opus"), ("All", "*.*")]
            )
            if not song:
                self.set_status("Cancelled")
                return
            self.log(f"Input: {song}")

        if mode == "reference":
            ref = filedialog.askopenfilename(
                title="Select reference track (optional, cancel for default)",
                filetypes=[("Audio files", "*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.opus"), ("All", "*.*")]
            )
            if ref:
                self.log(f"Reference: {ref}")

        if mode in ("batch", "album"):
            folder = filedialog.askdirectory(title="Select folder for batch/album analysis")
            if not folder:
                self.set_status("Cancelled")
                return
            self.log(f"Folder: {folder}")

        no_prev = self.no_previews_var.get()
        verbose = self.verbose_var.get()

        # Run in background thread
        def worker():
            try:
                self.queue.put(("log", "Initializing WorkflowRunner..."))
                logger = logging.getLogger("coproducer.gui")
                logger.handlers.clear()
                logger.addHandler(logging.NullHandler())

                runner = WorkflowRunner(ROOT, logger, generate_previews=not no_prev)

                if mode == "analyze":
                    rpt = runner.single(Path(song) if song else None)
                elif mode == "reference":
                    rpt = runner.reference(Path(song) if song else None, Path(ref) if ref else None)
                elif mode == "batch":
                    rpt = runner.batch(Path(folder) if folder else None)
                elif mode == "album":
                    rpt = runner.album(Path(folder) if folder else None)
                elif mode == "codecs":
                    rpt = runner.codecs(Path(song) if song else None)
                elif mode == "streaming":
                    rpt = runner.streaming(Path(song) if song else None)
                elif mode == "fixes":
                    rpt = runner.fixes(Path(song) if song else None)
                elif mode == "history":
                    rpt = runner.history()
                elif mode == "export":
                    rpt = runner.export()
                elif mode == "doctor":
                    rpt = runner.doctor()
                elif mode == "all":
                    rpt = runner.complete(
                        Path(song) if song else None,
                        Path(ref) if ref else None,
                        Path(folder) if folder else None
                    )
                else:
                    rpt = {"report_type": "unknown", "summary": "Mode not implemented in GUI yet"}

                self.queue.put(("done", rpt, mode))
            except Exception as exc:
                self.queue.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def open_last_report(self):
        if self.last_report_dir and self.last_report_dir.exists():
            import os
            os.startfile(self.last_report_dir)
        else:
            messagebox.showinfo("No report", "Run an analysis first.")

    def open_latest_html(self):
        reports = list((ROOT / "reports" / "html").glob("*.html"))
        if not reports:
            messagebox.showinfo("No reports", "No HTML reports found yet.")
            return
        latest = max(reports, key=lambda p: p.stat().st_mtime)
        import os
        os.startfile(latest)


if __name__ == "__main__":
    app = CoProducerGUI()
    app.mainloop()