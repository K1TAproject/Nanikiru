"""Same-window analysis controls and actual request/result debug views."""

import tkinter as tk
from tkinter import ttk

from nanikiru.analysis.dispatcher import METHOD_TASKS


class AnalysisPanel:
    def __init__(self, parent, outer, lower, run, changed):
        self.root = parent.winfo_toplevel()
        self.lower = lower
        frame = tk.Frame(parent, bg=parent.cget("bg"))
        frame.pack(fill="x", pady=4)
        row = tk.Frame(frame, bg=frame.cget("bg")); row.pack(fill="x")
        self.method = tk.StringVar(value="minimal-shape-v1")
        method = ttk.Combobox(row, textvariable=self.method, values=tuple(METHOD_TASKS), state="readonly", width=20)
        method.pack(side="left")
        method.bind("<<ComboboxSelected>>", lambda event: changed())
        self.run_button = ttk.Button(row, text="分析当前时点", command=run)
        self.run_button.pack(side="left", padx=4)
        self.auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="自动", variable=self.auto,
                        command=lambda: run() if self.auto.get() else None).pack(side="left")
        self.expanded = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="JSON", variable=self.expanded, command=self.toggle_details).pack(side="left")
        row = ttk.Frame(frame); row.pack(fill="x")
        self.method_options = tk.StringVar(value="{}")
        ttk.Label(row, text="v2 权重 JSON").pack(side="left")
        ttk.Entry(row, textvariable=self.method_options).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="应用", command=run).pack(side="left")
        row = tk.Frame(frame, bg=frame.cget("bg")); row.pack(fill="x", pady=3)
        self.tasks = {}
        for task, title in (("shanten", "向听"), ("tenpai", "等待"), ("discard_shape", "弃牌比较"), ("recommend", "牌效排序")):
            value = tk.BooleanVar(value=True)
            self.tasks[task] = value
            ttk.Checkbutton(row, text=title, variable=value, command=changed).pack(side="left", padx=(0, 4))
        self.input_summary = tk.StringVar(value="尚未构建请求")
        tk.Label(frame, textvariable=self.input_summary, bg=frame.cget("bg"), fg="#526a78",
                 anchor="w", justify="left", wraplength=470).pack(fill="x")
        self.summary = tk.StringVar()
        summary_frame = ttk.Frame(frame); summary_frame.pack(fill="x", pady=3)
        self.summary_text = tk.Text(summary_frame, height=6, width=1, wrap="word", state="disabled",
                                    bg=frame.cget("bg"), fg="#233c4a", font=("Microsoft YaHei UI", 9), relief="flat")
        scroll = ttk.Scrollbar(summary_frame, command=self.summary_text.yview)
        scroll.pack(side="right", fill="y")
        self.summary_text.configure(yscrollcommand=scroll.set)
        self.summary_text.pack(side="left", fill="x", expand=True)
        self.summary.trace_add("write", lambda *_: self.write(self.summary_text, self.summary.get()))

        self.details = tk.Frame(outer, bg=outer.cget("bg"))
        self.details.columnconfigure(0, weight=1, uniform="json")
        self.details.columnconfigure(1, weight=1, uniform="json")
        self.input_json = self._text_box("实际 AnalysisRequest（未知为 $unknown，非 null）", 0)
        self.output_json = self._text_box("实际 AnalysisResult / 调用错误", 1)

    def _text_box(self, title, column):
        frame = ttk.LabelFrame(self.details, text=title)
        frame.grid(row=0, column=column, sticky="nsew", padx=(0, 5) if column == 0 else (5, 0))
        frame.columnconfigure(0, weight=1); frame.rowconfigure(0, weight=1)
        widget = tk.Text(frame, height=5, width=1, wrap="none", font=("Consolas", 9), state="disabled")
        widget.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=widget.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        widget.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        return widget

    @staticmethod
    def write(widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def toggle_details(self):
        if self.expanded.get():
            height = min(max(self.root.winfo_height()+150, 900), self.root.winfo_screenheight()-70)
            self.root.minsize(1180, min(900, self.root.winfo_screenheight()-70))
            self.root.geometry(f"{self.root.winfo_width()}x{height}")
            self.details.pack(fill="x", after=self.lower, pady=(0, 5))
        else:
            self.details.pack_forget()
            self.root.minsize(1180, 760)
