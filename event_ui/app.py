"""Independent one-window manual event recorder: python -m event_ui."""

from pathlib import Path
import json
import tkinter as tk
from tkinter import ttk
from uuid import uuid4

from nanikiru.analysis import analyze
from nanikiru.analysis.output import debug_json, require_current_result, summarize_result
from nanikiru.models import UNKNOWN
from nanikiru.processing import Session, replay
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.tiles import index, tile34
from test_ui.tiles import TileButton, tile_name
from .initial import make_record, parse_code
from .analysis_panel import AnalysisPanel

BG, PAPER, INK = "#edf2f5", "#ffffff", "#233c4a"
PHASES = {"await_draw": "待摸牌", "await_discard": "待弃牌", "await_response": "待响应确认"}
HALTS = {"吃": "chi", "碰": "pon", "杠": "kan", "立直": "riichi", "和牌": "win", "流局": "draw_end", "其他": "other"}
CODES = ("?", *[tile34(i) for i in range(34)], "0m", "0p", "0s")


def text(parent, value, size=10, **kwargs):
    return tk.Label(parent, text=value, bg=parent.cget("bg"), fg=INK,
                    font=("Microsoft YaHei", size), **kwargs)


def card(parent, code, command=None, small=False):
    if code is not UNKNOWN:
        return TileButton(parent, code, command, small=small)
    w, h = (44, 58) if small else (58, 78)
    widget = tk.Canvas(parent, width=w, height=h, bg=parent.cget("bg"), highlightthickness=2)
    widget.create_rectangle(4, 4, w-4, h-4, fill="#fdfbf2", outline="#a3b3bf")
    widget.create_text(w/2, h/2, text="?", font=("Arial", 22), fill="#78909c")
    if command:
        widget.configure(takefocus=True, cursor="hand2")
        widget.bind("<Button-1>", lambda event: command())
        widget.bind("<Return>", lambda event: command())
    return widget


def clear(frame):
    for child in frame.winfo_children():
        child.destroy()


def name(code):
    return "未知牌" if code is UNKNOWN else tile_name(code)


class EventApp:
    def __init__(self, root, record=None):
        self.root = root
        root.title("Nanikiru · 人工事件记录")
        root.configure(bg=BG)
        root.geometry(f"1260x{min(900, root.winfo_screenheight()-90)}")
        root.minsize(1180, 760)
        root.option_add("*Font", ("Microsoft YaHei", 10))
        outer = tk.Frame(root, bg=BG, padx=18, pady=12)
        outer.pack(fill="both", expand=True)
        text(outer, "人工事件记录  /  MANUAL ROUND", 20, anchor="w").pack(fill="x")

        setup = tk.LabelFrame(outer, text="新建入口：简化无副露局面；未知牌用 ?，各家已有牌河仅保留本家顺序", bg=PAPER, padx=8, pady=5)
        setup.pack(fill="x", pady=(8, 5))
        self.hand_input = tk.StringVar(value="1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s")
        self.draw_input, self.indicator_input = tk.StringVar(value="9m"), tk.StringVar(value="6s")
        row = tk.Frame(setup, bg=PAPER); row.pack(fill="x")
        text(row, "自身13张").pack(side="left")
        ttk.Entry(row, textvariable=self.hand_input, width=52).pack(side="left", padx=5)
        text(row, "第14张").pack(side="left")
        ttk.Entry(row, textvariable=self.draw_input, width=4).pack(side="left", padx=5)
        text(row, "指示牌").pack(side="left")
        ttk.Entry(row, textvariable=self.indicator_input, width=4).pack(side="left", padx=5)
        self.entry_mode = tk.StringVar(value="开局")
        ttk.Combobox(row, textvariable=self.entry_mode, values=("开局", "中途"), state="readonly", width=5).pack(side="left", padx=5)
        self.entry_actor, self.entry_phase = tk.StringVar(value="0"), tk.StringVar(value="待弃牌")
        text(row, "行动家").pack(side="left")
        ttk.Combobox(row, textvariable=self.entry_actor, values=("0", "1", "2", "3"), width=2, state="readonly").pack(side="left")
        ttk.Combobox(row, textvariable=self.entry_phase, values=("待摸牌", "待弃牌"), width=7, state="readonly").pack(side="left", padx=5)
        ttk.Button(row, text="按输入新建", command=lambda: self.perform(self.new_record)).pack(side="right")
        row = tk.Frame(setup, bg=PAPER); row.pack(fill="x", pady=(5, 0))
        self.river_inputs = []
        for seat in range(4):
            text(row, f"{seat}家牌河").pack(side="left")
            value = tk.StringVar()
            self.river_inputs.append(value)
            ttk.Entry(row, textvariable=value, width=16).pack(side="left", padx=4)
        self.public_complete = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="中途公开牌已全部录入", variable=self.public_complete).pack(side="left", padx=4)

        storage = tk.Frame(outer, bg=BG); storage.pack(fill="x", pady=5)
        self.path_input = tk.StringVar(value=str(Path.cwd() / "manual_round.json"))
        text(storage, "记录路径").pack(side="left")
        ttk.Entry(storage, textvariable=self.path_input, width=55).pack(side="left", padx=5)
        self.overwrite = tk.BooleanVar(value=False)
        ttk.Checkbutton(storage, text="允许覆盖", variable=self.overwrite).pack(side="left")
        ttk.Button(storage, text="保存完整记录", command=lambda: self.perform(self.save)).pack(side="left", padx=5)
        ttk.Button(storage, text="加载记录", command=lambda: self.perform(self.load)).pack(side="left")
        self.undo_button = ttk.Button(storage, text="撤销最后一步", command=lambda: self.perform(self.undo))
        self.undo_button.pack(side="right")
        ttk.Button(storage, text="回到最新", command=self.latest).pack(side="right", padx=5)

        self.status = tk.StringVar()
        tk.Label(outer, textvariable=self.status, bg="#dce8ef", fg=INK, anchor="w",
                 padx=8, pady=6).pack(fill="x")
        controls = tk.Frame(outer, bg=BG); controls.pack(fill="x", pady=6)
        text(controls, "录入牌面").pack(side="left")
        self.code_input = tk.StringVar(value="1m")
        self.code_box = ttk.Combobox(controls, textvariable=self.code_input, values=CODES, width=5, state="readonly")
        self.code_box.pack(side="left", padx=5)
        self.draw_button = ttk.Button(controls, text="记录摸牌", command=lambda: self.perform(self.draw))
        self.draw_button.pack(side="left")
        self.discard_kind = tk.StringVar(value="未知")
        ttk.Combobox(controls, textvariable=self.discard_kind, values=("未知", "手切", "摸切"), state="readonly", width=5).pack(side="left", padx=5)
        self.discard_button = ttk.Button(controls, text="记录弃牌", command=lambda: self.perform(self.discard))
        self.discard_button.pack(side="left")
        self.close_button = ttk.Button(controls, text="确认无鸣牌 / 无和牌，继续", command=lambda: self.perform(self.close_response))
        self.close_button.pack(side="left", padx=8)
        self.halt_input = tk.StringVar(value="碰")
        ttk.Combobox(controls, textvariable=self.halt_input, values=tuple(HALTS), state="readonly", width=5).pack(side="left")
        self.halt_button = ttk.Button(controls, text="记录未支持动作并暂停", command=lambda: self.perform(self.halt))
        self.halt_button.pack(side="left", padx=5)

        table = tk.Frame(outer, bg="#e1eae4", padx=10, pady=6); table.pack(fill="x")
        text(table, "0 家（自己） · 点击选牌仅作分析；按“记录弃牌”才会推进。对手摸牌始终未知。", anchor="w").pack(fill="x")
        row = tk.Frame(table, bg=table.cget("bg")); row.pack(fill="x")
        self.hand_frame = tk.Frame(row, bg=table.cget("bg")); self.hand_frame.pack(side="left")
        self.draw_frame = tk.Frame(row, bg=table.cget("bg")); self.draw_frame.pack(side="left", padx=15)
        self.indicator_frame = tk.Frame(row, bg=table.cget("bg")); self.indicator_frame.pack(side="right")

        lower = tk.Frame(outer, bg=BG); lower.pack(fill="both", expand=True, pady=8)
        lower.columnconfigure(0, weight=3); lower.columnconfigure(1, weight=2)
        lower.rowconfigure(0, weight=1)
        left = tk.LabelFrame(lower, text="四家牌河（包含接入时已知牌；未知历史不会补造）", bg=PAPER)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        canvas = tk.Canvas(left, bg=PAPER, highlightthickness=0, height=230)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        self.rivers_frame = tk.Frame(canvas, bg=PAPER)
        river_window = canvas.create_window(0, 0, window=self.rivers_frame, anchor="nw")
        self.rivers_frame.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(river_window, width=event.width))
        right = tk.Frame(lower, bg=PAPER, padx=8, pady=5); right.grid(row=0, column=1, sticky="nsew")
        text(right, "事件历史 · 点击任意行只读回放", 12, anchor="w").pack(fill="x")
        history_area = tk.Frame(right, bg=PAPER); history_area.pack(fill="both", expand=True)
        self.history = ttk.Treeview(history_area, columns=("event",), show="tree headings", height=3, selectmode="browse")
        self.history.heading("#0", text="步数"); self.history.column("#0", width=45, stretch=False)
        self.history.heading("event", text="已确认事件"); self.history.column("event", width=330)
        bar = ttk.Scrollbar(history_area, orient="vertical", command=self.history.yview)
        self.history.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y"); self.history.pack(fill="both", expand=True)
        self.history.bind("<<TreeviewSelect>>", self.seek)
        self.analysis_panel = AnalysisPanel(right, outer, lower, self.run_analysis, self.analysis_controls_changed)
        self.analysis_text = self.analysis_panel.summary
        self.last_request = None
        self.last_result = None
        self.message = tk.StringVar(value="初始点数和未提供的历史资格保持未知；不推导完整行动许可或结算。")
        self.message_label = tk.Label(outer, textvariable=self.message, bg=BG, fg="#9a4a25", anchor="w", wraplength=1100)
        self.message_label.pack(fill="x", side="bottom", before=lower)
        self.selected_id = None
        self.session = Session(record) if record is not None else Session(self.build_record())
        self.cursor = len(self.session.record.events)
        self.refresh()

    def build_record(self):
        return make_record([parse_code(c) for c in self.hand_input.get().split()],
                           parse_code(self.draw_input.get()), parse_code(self.indicator_input.get()),
                           actor=int(self.entry_actor.get()),
                           phase="await_draw" if self.entry_phase.get() == "待摸牌" else "await_discard",
                           from_start=self.entry_mode.get() == "开局",
                           rivers=tuple(tuple(parse_code(c) for c in v.get().split()) for v in self.river_inputs),
                           public_complete=self.public_complete.get())

    def perform(self, action):
        try:
            action()
            self.message.set("操作完成。记录的是已发生事实；牌效计算不等于实际行动许可。")
        except (ValueError, OSError, TypeError) as error:
            self.message.set(f"未执行：{error}")

    def require_live(self):
        if self.cursor != len(self.session.record.events):
            raise ValueError("正在回放，请先回到最新；修改历史请撤销末尾事件")

    def new_record(self):
        new = Session(self.build_record())
        self.session = new
        self.latest()

    def draw(self):
        self.require_live()
        p = self.session.position
        own = p.actor == p.observer_seat
        self.session.append("draw", actor=p.actor, data={"tile_id": uuid4().hex if own else None,
                                                       "code": parse_code(self.code_input.get()) if own else UNKNOWN})
        self.latest()

    def discard(self):
        self.require_live()
        p = self.session.position
        if p.actor == p.observer_seat:
            tile = next((t for t in p.tiles if t.id == self.selected_id and t.zone == "hand"), None)
            if tile is None:
                raise ValueError("先选择自己的手牌")
            tile_id = tile.id
            code = parse_code(self.code_input.get()) if tile.code is UNKNOWN else tile.code
        else:
            tile_id, code = uuid4().hex, parse_code(self.code_input.get())
        kind = {"未知": UNKNOWN, "手切": "hand", "摸切": "tsumogiri"}[self.discard_kind.get()]
        self.session.append("discard", actor=p.actor, data={"tile_id": tile_id, "code": code, "discard_kind": kind})
        self.latest()

    def close_response(self):
        self.require_live()
        pending = self.session.position.pending
        if not isinstance(pending, dict):
            raise ValueError("没有待确认的弃牌响应")
        self.session.append("close_response", target_id=pending["trigger_event"])
        self.latest()

    def halt(self):
        self.require_live()
        self.session.append("halt", data={"reason": HALTS[self.halt_input.get()]})
        self.latest()

    def undo(self):
        self.require_live()
        self.session.undo()
        self.latest()

    def save(self):
        path = Path(self.path_input.get())
        if path.exists() and not self.overwrite.get():
            raise ValueError("文件已存在；确认路径后勾选允许覆盖")
        self.session.save(path)

    def load(self):
        self.session.load(self.path_input.get())
        self.latest()

    def latest(self):
        self.cursor = len(self.session.record.events)
        self.refresh()

    def seek(self, event=None):
        selection = self.history.selection()
        if selection and int(selection[0]) != self.cursor:
            self.cursor = int(selection[0])
            self.refresh()

    def refresh(self):
        self.invalidate_analysis()
        record = self.session.record
        self.position = replay(record, self.cursor)
        p = self.position
        self.analysis_version = f"{self.session.state_version}:{self.cursor}"
        live = self.cursor == len(record.events)
        phase = PHASES.get(p.phase, "暂停 / 未知阶段")
        source = "开局记录" if record.coverage["from_start"] is True else "中途 / 历史不全"
        self.status.set(f"{'最新' if live else '只读回放'}  ·  {self.cursor}/{len(record.events)} 步  ·  {phase}  ·  行动家 {p.actor if type(p.actor) is int else '—'}  ·  {source}  ·  版本 {self.analysis_version[:8]}:{self.cursor}")
        for button, enabled in ((self.draw_button, live and p.phase == "await_draw" and p.remaining_draws != 0),
                                (self.discard_button, live and p.phase == "await_discard"),
                                (self.close_button, live and p.phase == "await_response"),
                                (self.halt_button, live and p.phase in PHASES),
                                (self.undo_button, live and bool(record.events))):
            button.configure(state="normal" if enabled else "disabled")
        self.code_box.configure(state="disabled" if p.phase == "await_draw" and p.actor != 0 else "readonly")
        for frame in (self.hand_frame, self.draw_frame, self.indicator_frame, self.rivers_frame):
            clear(frame)
        own = sorted((t for t in p.tiles if t.zone == "hand" and t.seat == 0),
                     key=lambda t: (34 if t.code is UNKNOWN else index(t.code), t.id))
        draw_id = p.draw_context["tile_id"] if isinstance(p.draw_context, dict) and p.draw_context["player"] == 0 else None
        if self.selected_id not in {t.id for t in own}:
            self.selected_id = draw_id if draw_id in {t.id for t in own} else own[0].id if own else None
        self.hand_buttons = {}
        for tile in own:
            frame = self.draw_frame if tile.id == draw_id else self.hand_frame
            button = card(frame, tile.code, lambda tid=tile.id: self.select(tid))
            button.pack(side="left")
            self.hand_buttons[tile.id] = button
        for tile in p.tiles:
            if tile.zone == "dora":
                cell = tk.Frame(self.indicator_frame, bg=self.indicator_frame.cget("bg")); cell.pack(side="left")
                text(cell, "指示牌", 9).pack()
                card(cell, tile.code, small=True).pack()
        for seat in range(4):
            row = tk.Frame(self.rivers_frame, bg=PAPER); row.pack(fill="x", padx=5, pady=3)
            river = [t for t in p.tiles if t.zone == "river" and t.seat == seat]
            text(row, f"{seat}家\n{len(river)} 张", 10, width=5).pack(side="left", anchor="n")
            cells = tk.Frame(row, bg=PAPER); cells.pack(side="left")
            if not river:
                text(cells, "空" if p.completeness.get("public_tiles") is True else "暂无已录入牌（非完整历史）").pack(pady=5)
            for i, tile in enumerate(river):
                card(cells, tile.code, small=True).grid(row=i//10, column=i%10, padx=1)
        self.history.delete(*self.history.get_children())
        self.history.insert("", "end", iid="0", text="0", values=("初始 / 接入快照",))
        for n, event in enumerate(record.events, 1):
            if event.kind in ("draw", "discard"):
                value = f"{event.actor}家 {'摸' if event.kind == 'draw' else '切'} {name(event.data['code'])}"
                if event.kind == "discard":
                    value += " · " + {"hand": "手切", "tsumogiri": "摸切", UNKNOWN: "方式未知"}[event.data["discard_kind"]]
            else:
                value = "确认无鸣牌/无和牌" if event.kind == "close_response" else f"暂停：{event.data['reason']}"
            self.history.insert("", "end", iid=str(n), text=str(n), values=(value,))
        self.history.selection_set(str(self.cursor)); self.history.see(str(self.cursor))
        self.select(self.selected_id)
        if self.analysis_panel.auto.get():
            self.run_analysis()

    def select(self, tile_id):
        self.selected_id = tile_id
        for tid, button in self.hand_buttons.items():
            button.configure(highlightbackground="#d49a31" if tid == tile_id else button.master.cget("bg"),
                             highlightcolor="#d49a31")
        self.show_analysis()

    def invalidate_analysis(self):
        self.last_request = self.last_result = None
        self.analysis_text.set("当前时点尚未分析，请点击“分析当前时点”。")
        self.analysis_panel.input_summary.set("尚未构建当前请求")
        self.analysis_panel.write(self.analysis_panel.input_json, "尚未构建当前请求")
        self.analysis_panel.write(self.analysis_panel.output_json, "尚未计算当前结果")

    def analysis_controls_changed(self):
        self.invalidate_analysis()
        if self.analysis_panel.auto.get():
            self.run_analysis()

    def run_analysis(self):
        self.invalidate_analysis()
        try:
            request = build_analysis_request(
                self.session.record, source_version=self.session.state_version, through=self.cursor,
                tasks=tuple(task for task, value in self.analysis_panel.tasks.items() if value.get()),
                method=self.analysis_panel.method.get(),
                method_options=json.loads(self.analysis_panel.method_options.get())
                if self.analysis_panel.method.get() == 'fixed-v2' else {})
            self.last_request = request
            self.analysis_panel.write(self.analysis_panel.input_json, debug_json(request))
            coverage = request.position.completeness.get("public_tiles", UNKNOWN)
            self.analysis_panel.input_summary.set(
                f"输入：视角 {request.position.observer_seat} · 截至 {len(request.history)} 条事件 · "
                f"公开覆盖 {'完整' if coverage is True else '不完整/未知'}")
            result = analyze(request)
            self.present_result(result)
        except (ValueError, TypeError) as error:
            self.last_result = None
            self.analysis_text.set(f"本次调用未产生结果：{error}")
            self.analysis_panel.write(self.analysis_panel.output_json, debug_json({"error": str(error)}))

    def present_result(self, result):
        if self.last_request is None:
            raise ValueError("当前没有已构建的分析请求")
        require_current_result(result, f"{self.session.state_version}:{self.cursor}", self.analysis_panel.method.get())
        self.last_result = result
        self.analysis_panel.write(self.analysis_panel.output_json, debug_json(result))
        self.show_analysis()

    def show_analysis(self):
        if self.last_request is None or self.last_result is None:
            return
        require_current_result(self.last_result, f"{self.session.state_version}:{self.cursor}", self.analysis_panel.method.get())
        self.analysis_text.set(summarize_result(self.last_request, self.last_result, self.selected_id))


def main():
    root = tk.Tk()
    EventApp(root)
    root.mainloop()
