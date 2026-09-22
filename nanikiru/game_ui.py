"""Canvas table with manual play and baseline bots; no rules live in the UI."""

from pathlib import Path
import json
import os
from queue import Empty, Queue
import subprocess
import sys
from time import monotonic
from threading import Thread
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from nanikiru.bot import choose_action, choose_yaku_action
    from nanikiru.game import Game
    from nanikiru.models import Decision, Discard, Tile
    from nanikiru.scoring import Rules, WINDS as WIND_NAMES
    from nanikiru.efficiency import summarize_reviews
    from nanikiru.decision import STRATEGY_VERSION
    from nanikiru.comparison_ui import ComparisonPanel
    from nanikiru.preview_ui import PreviewPanel
    from nanikiru.riichi_decision import choose_riichi_action, VERSION as RIICHI_VERSION
else:
    from .bot import choose_action, choose_yaku_action
    from .game import Game
    from .models import Decision, Discard, Tile
    from .scoring import Rules, WINDS as WIND_NAMES
    from .efficiency import summarize_reviews
    from .decision import STRATEGY_VERSION
    from .comparison_ui import ComparisonPanel
    from .preview_ui import PreviewPanel
    from .riichi_decision import choose_riichi_action, VERSION as RIICHI_VERSION


PROJECT = Path(__file__).resolve().parents[1]
WINDS = ("东", "南", "西", "北")
ACTION_NAMES = {"pass": "过", "chi": "吃", "pon": "碰", "open_kan": "明杠", "closed_kan": "暗杠",
                "added_kan": "加杠", "riichi": "立直", "tsumo": "自摸", "ron": "荣和", "nine_terminals": "九种九牌"}
RESULT_NAMES = {"tsumo": "自摸", "ron": "荣和", "exhaustive": "荒牌流局", "nagashi_mangan": "流局满贯",
                "nine_terminals": "九种九牌", "four_winds": "四风连打", "four_riichi": "四家立直",
                "four_kans": "四杠散了", "three_ron": "三家荣和流局"}
YAKU_NAMES = {"Riichi": "立直", "Double Riichi": "双立直", "Ippatsu": "一发", "Menzen Tsumo": "门前清自摸和",
              "Tanyao": "断幺九", "Pinfu": "平和", "Iipeiko": "一杯口", "Ryanpeikou": "二杯口",
              "Chankan": "抢杠", "Rinshan Kaihou": "岭上开花", "Haitei Raoyue": "海底摸月", "Houtei Raoyui": "河底捞鱼",
              "Sanshoku Doujun": "三色同顺", "Sanshoku Doukou": "三色同刻", "Ittsu": "一气通贯",
              "Chantai": "混全带幺九", "Junchan": "纯全带幺九", "Chiitoitsu": "七对子", "Toitoi": "对对和",
              "San Ankou": "三暗刻", "San Kantsu": "三杠子", "Honroutou": "混老头", "Shou Sangen": "小三元",
              "Honitsu": "混一色", "Chinitsu": "清一色", "Dora": "宝牌", "Ura Dora": "里宝", "Aka Dora": "赤宝",
              "Yakuhai (haku)": "役牌·白", "Yakuhai (hatsu)": "役牌·发", "Yakuhai (chun)": "役牌·中",
              "Tenhou": "天和", "Chiihou": "地和", "Daisangen": "大三元", "Dai Suushii": "大四喜",
              "Shousuushii": "小四喜", "Suu Ankou": "四暗刻", "Suu Ankou Tanki": "四暗刻单骑",
              "Suu Kantsu": "四杠子", "Tsuu Iisou": "字一色", "Chinroutou": "清老头", "Ryuuiisou": "绿一色",
              "Kokushi Musou": "国士无双", "Kokushi Musou Juusanmen Matchi": "国士无双十三面",
              "Chuuren Poutou": "九莲宝灯", "Daburu Chuuren Poutou": "纯正九莲宝灯"}
YAKU_NAMES.update({f"Yakuhai ({kind} wind {wind})": f"{label}·{WINDS[i]}"
                   for kind, label in (("seat", "自风"), ("round", "场风")) for i, wind in enumerate(WIND_NAMES)})


class GameWindow:
    def __init__(self, root, game=None):
        self.root, self.game = root, game or Game()
        self.selection = None
        self.preview = None
        self.dirty = False
        self.testing = False
        self.lesson_pending = None
        self.lesson_enabled = tk.BooleanVar(value=False)
        self.auto_pass = tk.BooleanVar(value=True)
        self.queue = Queue()
        self.human_seat = self.game.observe(0)["dealer_seat"]
        self.view = tk.StringVar(value=f"玩家 {self.human_seat}")
        self.bots_enabled = tk.BooleanVar(value=True)
        self.next_bot_time = 0.0
        self.sort_tiles = tk.BooleanVar(value=True)
        self.notice = tk.StringVar(value="请选择当前行动玩家的手牌，再确认弃牌。")
        self.action_builders = {}
        root.title("Nanikiru · 图形牌桌")
        width, height = min(1200, root.winfo_screenwidth() - 80), min(900, root.winfo_screenheight() - 100)
        root.geometry(f"{width}x{height}")
        root.minsize(min(960, width), min(760, height))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.configure(bg="#edf1ef")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei", 10), background="#edf1ef", foreground="#243e37")
        style.configure("TButton", padding=(10, 6), relief="flat", background="#e0e9e4")
        style.map("TButton", background=[("active", "#ceded4")])
        style.configure("Accent.TButton", background="#235d49", foreground="white")
        style.map("Accent.TButton", background=[("active", "#34765b"), ("disabled", "#c2ccc6")])
        style.configure("TNotebook", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8))
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")])
        style.configure("Treeview", rowheight=30, background="#ffffff", fieldbackground="#ffffff")
        style.configure("Treeview.Heading", padding=6, font=("Microsoft YaHei", 10, "bold"))
        toolbar = ttk.Frame(root, padding=(12, 10))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="NANIKIRU", font=("Segoe UI", 15, "bold")).pack(side="left", padx=(0, 18))
        for title, callback in (("随机新局", self.random_game), ("加载", self.load),
                                ("保存", self.save), ("暂停 / 恢复", self.pause),
                                ("撤销动作", self.undo)):
            ttk.Button(toolbar, text=title, width=8, command=callback).pack(side="left", padx=2)
        ttk.Button(toolbar, text="设置", width=6, command=lambda: self.open_page(self.settings_frame)).pack(side="right", padx=3)
        debug_menu = tk.Menu(root, tearoff=False)
        debug_button = ttk.Menubutton(toolbar, text="开发 / 调试", width=12, menu=debug_menu)
        debug_button.pack(side="right", padx=3)
        for label, attr in (("当前视角事件", "event_text"), ("当前视角 JSON", "json_text"), ("自动化测试", "test_frame")):
            debug_menu.add_command(label=label, command=lambda a=attr: self.open_page(getattr(self, a)))
        debug_menu.add_separator()
        debug_menu.add_command(label="收起调试页面", command=self.close_auxiliary)
        self.tabs = ttk.Notebook(root)
        self.settings_frame = ttk.Frame(self.tabs, padding=12)
        ttk.Label(self.settings_frame, text="对局设置", font=("Microsoft YaHei", 18, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(self.settings_frame, text="规则开关仅在新局生效；模式切换只影响后续决策。").pack(anchor="w", pady=(0, 8))
        ttk.Button(self.settings_frame, text="按种子开局", command=self.new_game).place(x=220, y=12)
        toolbar = ttk.LabelFrame(self.settings_frame, text="视角与牌面", padding=8)
        toolbar.pack(fill="x", pady=2)
        ttk.Label(toolbar, text="视角：").pack(side="left", padx=(12, 0))
        box = ttk.Combobox(toolbar, textvariable=self.view, width=13, state="readonly",
                           values=[f"玩家 {i}" for i in range(4)] + ["全知调试"])
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda event: self.refresh())
        ttk.Checkbutton(toolbar, text="自动理牌", variable=self.sort_tiles,
                        command=self.refresh).pack(side="left", padx=8)
        toolbar = ttk.LabelFrame(self.settings_frame, text="新局规则", padding=8)
        toolbar.pack(fill="x", pady=2)
        self.aka_enabled = tk.BooleanVar(value=self.game.rules.aka_dora_enabled)
        self.kan_enabled = tk.BooleanVar(value=self.game.rules.kan_dora_enabled)
        ttk.Checkbutton(toolbar, text="新局计赤宝", variable=self.aka_enabled).pack(side="left")
        ttk.Checkbutton(toolbar, text="新局开杠宝", variable=self.kan_enabled).pack(side="left")
        self.round_wind = tk.StringVar(value=WINDS[WIND_NAMES.index(self.game.debug_state().round_wind)])
        ttk.Label(toolbar, text="新局场风：").pack(side="left")
        ttk.Combobox(toolbar, textvariable=self.round_wind, values=WINDS, width=3, state="readonly").pack(side="left")
        toolbar = ttk.LabelFrame(self.settings_frame, text="机器人", padding=8)
        toolbar.pack(fill="x", pady=2)
        ttk.Checkbutton(toolbar, text="三家机器人", variable=self.bots_enabled,
                        command=self.refresh).pack(side="left", padx=8)
        self.bot_strategy = tk.StringVar(value="基础立直")
        strategy_box = ttk.Combobox(toolbar, textvariable=self.bot_strategy, values=("基础立直", "役种感知", "基础牌效"),
                     state="readonly", width=8)
        strategy_box.pack(side="left")
        strategy_box.bind("<<ComboboxSelected>>", lambda event: self.update_mode_controls())
        ttk.Checkbutton(toolbar, text="人工弃牌后等待继续", variable=self.lesson_enabled).pack(side="left", padx=12)
        modes = ttk.LabelFrame(self.settings_frame, text="评估模式", padding=6)
        modes.pack(fill="x")
        self.human_mode = tk.StringVar(value="进攻")
        self.bot_mode = tk.StringVar(value="进攻")
        ttk.Label(modes, text="人工复盘模式：").pack(side="left")
        ttk.Combobox(modes, textvariable=self.human_mode, values=("进攻", "弃和"), state="readonly", width=6).pack(side="left")
        ttk.Label(modes, text="  三家役种机器人模式：").pack(side="left")
        self.bot_mode_box = ttk.Combobox(modes, textvariable=self.bot_mode, values=("进攻", "弃和"), state="readonly", width=6)
        self.bot_mode_box.pack(side="left")
        self.mode_hint = ttk.Label(self.settings_frame, text="仅影响后续决策；人工模式不代替操作")
        self.mode_hint.pack(anchor="w", pady=2)
        ttk.Checkbutton(modes, text="仅有过时自动响应", variable=self.auto_pass).pack(side="left", padx=8)
        self.banner = tk.Label(root, anchor="w", padx=10, pady=5)
        self.banner.pack(fill="x")
        self.training_frame = ttk.Frame(self.tabs)
        self.feedback_frame = ttk.Frame(self.training_frame, padding=(14, 8))
        self.feedback_frame.pack(side="bottom", fill="x")
        ttk.Button(self.feedback_frame, text="展开复盘 →", command=self.open_feedback).pack(side="right", padx=(12, 0))
        ttk.Button(self.feedback_frame, text="候选比较", command=self.open_comparison).pack(side="right", padx=4)
        ttk.Button(self.feedback_frame, text="推进／立直", command=self.open_preview).pack(side="right", padx=4)
        self.feedback_text = ScrolledText(self.feedback_frame, height=3, wrap="word", relief="flat",
                                          bg="#f5f8f5", fg="#284b3d", font=("Microsoft YaHei", 10), borderwidth=0)
        self.feedback_text.pack(fill="x", expand=True)
        self.canvas = tk.Canvas(self.training_frame, bg="#143d34", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.event_text = ScrolledText(self.tabs, wrap="word", font=("Microsoft YaHei", 11))
        self.json_text = ScrolledText(self.tabs, wrap="none")
        self.test_frame = ttk.Frame(self.tabs, padding=12)
        self.test_text = ScrolledText(self.test_frame, wrap="word")
        self.test_text.pack(fill="both", expand=True)
        self.result_frame = ttk.Frame(self.tabs, padding=20)
        self.result_summary = tk.StringVar()
        ttk.Label(self.result_frame, text="单局结算", font=("Microsoft YaHei", 20, "bold")).pack(anchor="w", pady=(0, 16))
        ttk.Label(self.result_frame, textvariable=self.result_summary, font=("Microsoft YaHei", 12), justify="left").pack(anchor="w", pady=12)
        self.result_table = ttk.Treeview(self.result_frame, columns=("seat", "start", "delta", "end"), show="headings", height=4)
        for column, label in (("seat", "玩家"), ("start", "开局点数"), ("delta", "本局变化（含立直支出）"), ("end", "结束点数")):
            self.result_table.heading(column, text=label)
            self.result_table.column(column, width=150, anchor="center")
        self.result_table.pack(fill="x", pady=8)
        ttk.Button(self.result_frame, text="展开 / 收起番符与支付明细", command=self.toggle_result_details).pack(anchor="w", pady=10)
        self.result_text = ScrolledText(self.result_frame, wrap="word", font=("Microsoft YaHei", 11))
        self.decision_frame = ttk.Frame(self.tabs, padding=8)
        self.decision_selector = ttk.Combobox(self.decision_frame, state="readonly", width=65)
        self.decision_selector.pack(fill="x")
        self.decision_selector.bind("<<ComboboxSelected>>", lambda event: self.show_decision())
        self.decision_text = ScrolledText(self.decision_frame, wrap="word", font=("Microsoft YaHei", 11))
        self.decision_text.pack(fill="both", expand=True)
        self.review_frame = ttk.Frame(self.tabs, padding=8)
        self.tenpai_frame = ttk.Frame(self.tabs, padding=8)
        self.tenpai_selector = ttk.Combobox(self.tenpai_frame, state="readonly", width=65)
        self.tenpai_selector.pack(fill="x")
        self.tenpai_selector.bind("<<ComboboxSelected>>", lambda event: self.show_tenpai())
        self.tenpai_text = ScrolledText(self.tenpai_frame, wrap="word", font=("Microsoft YaHei", 11))
        self.tenpai_text.pack(fill="both", expand=True)
        for widget, title in ((self.training_frame, "牌桌训练"), (self.event_text, "当前视角事件"),
                              (self.json_text, "当前视角数据"), (self.result_frame, "单局结算"),
                              (self.review_frame, "逐切复盘"), (self.tenpai_frame, "听牌质量"),
                              (self.decision_frame, "役种决策复盘"), (self.test_frame, "测试输出"), (self.settings_frame, "设置")):
            self.tabs.add(widget, text=title)
        for widget in (self.event_text, self.json_text, self.test_frame, self.settings_frame):
            self.tabs.hide(widget)
        self.settings_done = ttk.Button(self.settings_frame, text="完成 · 返回牌桌", command=self.close_auxiliary)
        self.settings_done.place(relx=1, x=-12, y=12, anchor="ne")
        ttk.Label(self.review_frame, text="基础牌效：先最低向听，再最多有效进张。仅切后反馈，不代表综合最优；未见枚数不是实际牌山余量。",
                  wraplength=880).pack(fill="x")
        self.review_summary = tk.StringVar()
        ttk.Label(self.review_frame, textvariable=self.review_summary, justify="left", wraplength=880).pack(fill="x", pady=8)
        review_controls = ttk.Frame(self.review_frame)
        review_controls.pack(fill="x")
        self.review_selector = ttk.Combobox(review_controls, state="readonly", width=45)
        self.review_selector.pack(side="left")
        self.review_selector.bind("<<ComboboxSelected>>", lambda event: self.show_review())
        ttk.Button(review_controls, text="上一切", command=lambda: self.step_review(-1)).pack(side="left", padx=4)
        ttk.Button(review_controls, text="下一切", command=lambda: self.step_review(1)).pack(side="left")
        self.review_description = tk.StringVar()
        ttk.Label(self.review_frame, textvariable=self.review_description, justify="left", wraplength=880).pack(fill="x", pady=8)
        table = ttk.Frame(self.review_frame)
        table.pack(fill="both", expand=True)
        self.review_table = ttk.Treeview(table, columns=("tile", "source", "shanten", "ukeire", "best", "effective"), show="headings")
        for key, label, width in (("tile", "弃牌", 60), ("source", "来源", 70), ("shanten", "切后向听", 75),
                                  ("ukeire", "进张枚数", 75), ("best", "基础判定", 100), ("effective", "有效牌种 × 未见枚数", 630)):
            self.review_table.heading(key, text=label)
            self.review_table.column(key, width=width, stretch=key == "effective")
        self.review_table.tag_configure("chosen", background="#fff0c2")
        yscroll = ttk.Scrollbar(table, orient="vertical", command=self.review_table.yview)
        xscroll = ttk.Scrollbar(table, orient="horizontal", command=self.review_table.xview)
        self.review_table.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.review_table.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.comparison_panel = ComparisonPanel(self.tabs, self.open_preview_history)
        self.tabs.add(self.comparison_panel, text="候选比较")
        self.preview_panel = PreviewPanel(self.tabs)
        self.canvas.bind("<Configure>", lambda event: self.draw_table())
        self.lesson_bar = ttk.Frame(root, padding=(8, 4))
        self.lesson_label = ttk.Label(self.lesson_bar, text="讲解停顿 · 弃牌已记录，尚未推进后续响应")
        self.lesson_label.pack(side="left")
        self.lesson_button = ttk.Button(self.lesson_bar, text="继续对局", command=self.continue_lesson)
        self.lesson_button.pack(side="right")
        controls = ttk.Frame(root, padding=6)
        controls.pack(side="bottom", fill="x")
        self.submit_button = ttk.Button(controls, text="确认弃牌", style="Accent.TButton", command=self.submit)
        self.submit_button.pack(side="left", padx=3)
        self.view_button = ttk.Button(controls, text="返回庄家视角", command=self.follow_actor)
        self.view_button.pack(side="left", padx=3)
        self.special = tk.StringVar(value="吃")
        self.special_box = ttk.Combobox(controls, state="readonly", textvariable=self.special, width=29,
                                       values=("吃", "碰", "明杠", "暗杠", "加杠", "立直", "自摸", "荣和", "流局"))
        self.special_box.pack(side="left", padx=3)
        ttk.Button(controls, text="执行所选动作", command=self.submit_special).pack(side="left", padx=3)
        self.test_button = ttk.Button(self.test_frame, text="运行自动化测试", command=self.run_tests)
        self.test_button.pack(side="bottom", anchor="e", pady=8)
        ttk.Label(root, textvariable=self.notice, padding=(10, 3)).pack(side="bottom", fill="x")
        self.history_frame = history = ttk.Frame(root, padding=6)
        ttk.Button(controls, text="回放工具", command=self.toggle_history).pack(side="right")
        ttk.Label(history, text="已执行动作数（含过）：").pack(side="left")
        self.history = ttk.Combobox(history, state="readonly", width=8)
        self.history.pack(side="left")
        for title, callback in (("只读回放", self.show_history), ("返回当前", self.live), ("回退重录", self.rewind)):
            ttk.Button(history, text=title, command=callback).pack(side="left", padx=3)
        self.tabs.pack(fill="both", expand=True, padx=6)
        self.tabs.select(self.training_frame)
        self.refresh()
        self.poll_id = root.after(100, self.poll)

    def begin_lesson(self, action):
        if (self.lesson_enabled.get() and not self.game.observe(action.player)['result']
                and (isinstance(action, Discard) or isinstance(action, Decision) and action.kind == 'riichi')):
            self.lesson_pending = (id(self.game), self.game.action_count)

    def continue_lesson(self):
        if self.preview is not None or self.game.paused or self.testing or self.data['result']:
            self.notice.set("请先退出回放、显式恢复核心暂停或等待测试完成；继续讲解不会解除这些限制。")
            return
        self.lesson_pending = None
        self.refresh()

    def open_comparison(self):
        self.tabs.select(self.comparison_panel)
        if self.feedback_record:
            self.comparison_panel.focus_record(self.feedback_record['action_index'])

    def open_page(self, widget):
        self.tabs.add(widget)
        self.tabs.select(widget)

    def close_auxiliary(self):
        self.tabs.select(self.training_frame)
        for widget in (self.settings_frame, self.event_text, self.json_text, self.test_frame):
            self.tabs.hide(widget)

    def toggle_history(self):
        if self.history_frame.winfo_manager():
            self.history_frame.pack_forget()
        else:
            self.history_frame.pack(side="bottom", fill="x", before=self.tabs)

    def open_feedback(self):
        self.tabs.select(self.review_frame)
        if self.feedback_record:
            index = next(i for i, r in enumerate(self.visible_reviews)
                         if r['action_index'] == self.feedback_record['action_index'])
            self.review_selector.current(index)
            self.tenpai_selector.current(index)
            self.show_review()
            self.show_tenpai()
            for i, r in enumerate(self.visible_decisions):
                if r['action_index'] == self.feedback_record['action_index']:
                    self.decision_selector.current(i)
                    self.show_decision()
                    break

    def show_feedback(self):
        manual = {r['action_index']: r for r in self.visible_decisions if r['policy'] == 'manual'}
        records = [r for r in self.visible_reviews if r['action_index'] in manual]
        self.feedback_record = records[-1] if records else None
        if not records:
            self.put(self.feedback_text, "训练反馈\n当前视角尚无人工弃牌分析。出牌后显示基础牌效；旧记录来源未知时不推断人工操作。")
            return
        r = records[-1]
        choice = r['comparison']['selected']
        best = list(dict.fromkeys(str(Tile(**c['discard']['tile'])) for c in r['analysis']['candidates'] if c['is_best']))
        message = (f"最近人工弃牌 · 玩家 {r['player']} · 切 {Tile(**choice['discard']['tile'])}  |  "
                   + self.review_comparison(r))
        if not choice['is_best']:
            message += f"\n基础最优候选：{' / '.join(best)} · {r['analysis']['best_shanten']} 向听 / {r['analysis']['best_ukeire']} 枚进张。"
        else:
            message += f"\n基础最优候选：{' / '.join(best)}。"
        defense = manual[r['action_index']].get('defense')
        message += (" 弃和模式：牌效损失不等于综合决策错误。" if defense and defense['mode'] == 'fold'
                    else " 仅评价基础牌效，不代表综合最优。")
        self.put(self.feedback_text, message)

    def open_preview(self):
        self.tabs.add(self.preview_panel, text="推进／立直")
        self.tabs.select(self.preview_panel)
        if (self.preview is None and not self.game.paused and not self.lesson_pending
                and self.data['phase'] == 'await_discard' and self.data["actor"] == self.seat and self.manual_turn()):
            self.preview_panel.show_live(self.game.observe(self.seat), self.game.legal_actions(self.seat), self.submission_mode())

    def open_preview_history(self):
        self.tabs.add(self.preview_panel, text="推进／立直")
        self.tabs.select(self.preview_panel)
        panel = self.comparison_panel
        if panel.records and panel.record_box.current() >= 0 and panel.candidate_box.current() >= 0:
            self.preview_panel.focus_record(panel.records[panel.record_box.current()]['action_index'],
                                            panel.choices[panel.candidate_box.current()])

    def update_mode_controls(self):
        basic = self.bot_strategy.get() == "基础牌效"
        self.bot_mode_box.configure(state="disabled" if basic else "readonly")
        self.mode_hint.configure(text="基础牌效机器人不应用攻守模式" if basic else "仅影响后续决策；人工模式不代替操作")

    def submission_mode(self):
        return "fold" if self.human_mode.get() == "弃和" else "attack"

    def visible_game(self):
        return self.game if self.preview is None else self.game.replay(self.preview)

    def refresh(self):
        context = (id(self.game), self.view.get(), self.preview)
        retained = {name: getattr(self, name).get() for name in
                    ('review_selector', 'tenpai_selector', 'decision_selector')} if getattr(self, '_review_context', None) == context else {}
        self._review_context = context
        self._retained_selectors = retained
        self.selection = None
        if self.lesson_pending and (self.lesson_pending[0] != id(self.game) or self.lesson_pending[1] != self.game.action_count):
            self.lesson_pending = None
        if self.lesson_pending:
            self.lesson_bar.pack(side="bottom", fill="x", before=self.tabs)
            self.lesson_button.configure(state="disabled" if self.game.paused or self.preview is not None or self.testing else "normal")
        else:
            self.lesson_bar.pack_forget()
        game = self.visible_game()
        self.omni = self.view.get() == "全知调试"
        self.seat = 0 if self.omni else int(self.view.get()[-1])
        self.data = game.debug_view() if self.omni else game.observe(self.seat)
        mode = "全知调试 · 包含四家暗手与未公开牌山，禁止作为玩家输入" if self.omni else f"玩家 {self.seat} 视角 · 其余暗手已隐藏"
        self.banner.configure(text=mode + (f" | 人工：庄家 {self.human_seat}；三家机器人" if self.bots_enabled.get() else " | 全人工调试") + (f" | 只读回放 {self.preview} 手" if self.preview is not None else "")
                              + (" | 已暂停" if self.game.paused else "")
                              + (" | 讲解停顿" if self.lesson_pending else "")
                              + (" | 未保存" if self.dirty else ""),
                              bg="#ffe0a3" if self.omni else "#e4eee8", fg="#4c3000" if self.omni else "#234538")
        self.put(self.json_text, json.dumps(self.data, ensure_ascii=False, indent=2))
        lines = []
        for i, e in enumerate(self.data["events"], 1):
            if e["type"] not in ("draw", "discard"):
                lines.append(f"{i:03}  {json.dumps(e, ensure_ascii=False)}")
                continue
            tile = str(Tile(**e["tile"])) if e["tile"] else "未知牌"
            suffix = "" if e["type"] == "draw" else "（摸切）" if e["is_tsumogiri"] else "（手切）"
            lines.append(f"{i:03}  玩家 {e['player']}  {'摸入' if e['type'] == 'draw' else '弃出'} {tile}{suffix}")
        self.put(self.event_text, "\n".join(lines))
        self.history["values"] = [str(i) for i in range(self.game.action_count + 1)]
        self.history.set(str(self.game.action_count if self.preview is None else self.preview))
        self.submit_button.configure(state="disabled")
        self.view_button.configure(text="返回庄家视角" if self.bots_enabled.get() else "切到行动玩家")
        # Clear prior selection messages on perspective change: do not retain hidden tiles.
        self.notice.set("本局已结束，查看单局结算。" if self.data["result"] else
                        f"玩家 {self.data['actor']} {'待响应（须选择动作或过）' if self.data['phase'] == 'await_response' else '待行动'}；切换至该玩家视角或全知调试。")
        if self.bots_enabled.get() and not self.data["result"]:
            if self.data["actor"] != self.human_seat:
                self.notice.set(f"玩家 {self.data['actor']} 由机器人操作；主视角保持不变。")
            else:
                self.notice.set("轮到庄家操作：请选择弃牌，或从下方菜单响应 / 过。")
        self.decision_options = {}
        if not self.lesson_pending and self.preview is None and self.manual_turn() and (self.omni or self.seat == self.data["actor"]):
            for action in game.legal_actions(self.data["actor"]):
                if isinstance(action, Decision):
                    label = ACTION_NAMES[action.kind] + (" " + " ".join(map(str, action.tiles)) if action.tiles else "")
                    if action.kind == "riichi":
                        label += "（摸切）" if action.is_tsumogiri else "（手切）"
                    self.decision_options[label] = action
        self.special_box["values"] = list(self.decision_options) + list(self.action_builders)
        self.special.set(next(iter(self.decision_options), "无可用特殊动作"))
        self.show_result()
        self.show_reviews(game)
        self.show_decisions(game)
        self.show_feedback()
        self.comparison_panel.update_records(self.visible_reviews, self.visible_decisions, context)
        self.preview_panel.update_records(self.visible_reviews, self.visible_decisions, context)
        self.draw_table()

    def show_decisions(self, game):
        self.visible_decisions = game.debug_decisions() if self.omni else game.decision_records(self.seat)
        self.decision_selector["values"] = [f"动作 {r['action_index']} · 玩家 {r['player']} · {r['actual_action'].get('kind', '弃牌')}"
                                              for r in self.visible_decisions]
        if self.visible_decisions:
            previous = self._retained_selectors.get("decision_selector")
            self.decision_selector.current(list(self.decision_selector["values"]).index(previous) if previous in self.decision_selector["values"] else len(self.visible_decisions) - 1)
        else:
            self.decision_selector.set("")
        self.show_decision()

    @staticmethod
    def action_label(action):
        if action is None:
            return "回退基础策略"
        if action["type"] == "discard":
            return "切 " + str(Tile(**action["tile"])) + ("（摸切）" if action["is_tsumogiri"] else "（手切）")
        return ACTION_NAMES[action["kind"]] + " " + " ".join(str(Tile(**t)) for t in action["tiles"])

    def show_decision(self):
        if not self.visible_decisions:
            self.put(self.decision_text, "当前视角尚无已提交的役种决策记录。")
            return
        r = self.visible_decisions[self.decision_selector.current()]
        a = r["analysis"]
        lines = ["开发基准，不代表综合最优。潜在役需要后续进张，宝牌不是役；役种分析本身不评估防守。",
                 f"实际：{self.action_label(r['actual_action'])}；操作策略：{r['policy']}",
                 f"原进攻建议：{self.action_label(a['recommended'])}；版本：{a['version']}", a["reason"],
                 "吃碰记录表示已提交意向，不等于最终鸣牌成功；需查看响应裁决。", ""]
        defense = r.get("defense")
        if defense:
            lines += ["防守 / 攻守模式（开发基准）：" + ("弃和" if defense["mode"] == "fold" else "进攻"),
                      f"版本：{defense['version']}；已成立立直威胁：{defense['threats']}", defense["scope"],
                      "模式建议：" + self.action_label(defense["recommended"]), defense["reason"],
                      "基础牌效损失不等同于综合决策错误。"]
            for c in defense["candidates"]:
                actual = " [实际]" if c["action"] == r["actual_action"] else ""
                lines.append(self.action_label(c["action"]) + actual + "：" + "；".join(
                    f"对玩家 {e['player']}：{e['reason']}" for e in c["opponents"]))
            lines += ["", "以下为原进攻役种分析（不代表弃和模式建议）："]
        else:
            lines += ["本记录无攻守模式：旧记录未保存，或基础牌效策略不适用。", ""]
        statuses = {"condition_met": "条件已满足", "potential": "潜在路径", "unavailable_now": "当前不可用",
                    "blocked_by_meld": "副露已阻断"}
        def describe(c, prefix=""):
            p = c["paths"]
            lines.append(f"{prefix}{self.action_label(c['action'])}：{c['shanten']} 向听，进张 {c['ukeire']} 枚" +
                         (" [基准优选]" if c.get("preferred") else ""))
            lines.append("  " + c["reason"])
            honors = [f"{h['tile']}({'/'.join(h['roles'])}) {statuses[h['status']]}" for h in p["yakuhai"]
                      if h["concealed_count"] or h["status"] == "condition_met"]
            lines.append("  役牌：" + ("；".join(honors) or "无当前持有路径") +
                         f"；断幺九：{statuses[p['tanyao']]}；立直：{statuses[p['riichi']]}")
            lines.append(f"  宝牌线索 {p['dora_tiles']} 张，赤牌 {p['red_tiles']} 张（当前计赤宝 {p['aka_bonus_tiles']}）；其他役未评估")
            if c.get("lost_closed_paths"):
                lines.append("  开门后不再可用：" + "、".join(c["lost_closed_paths"]))
        for c in a["candidates"]:
            describe(c)
            for d in c.get("discard_candidates", []):
                describe(d, "    鸣牌后 ")
            lines.append("")
        self.put(self.decision_text, "\n".join(lines))

    @staticmethod
    def review_comparison(record):
        c = record["comparison"]
        selected = c["selected"]
        if c["forced"]:
            verdict = "仅一种合法弃牌，单独统计"
        elif c["shanten_increase"]:
            verdict = f"比最低向听增加 {c['shanten_increase']} 向听；进张不跨向听比较"
        elif selected["is_best"]:
            verdict = "基础牌效最优（允许并列）"
        else:
            verdict = f"同向听下少 {c['ukeire_loss']} 枚有效进张"
        return f"{selected['shanten']} 向听，进张 {selected['ukeire']} 枚；{verdict}"

    def show_reviews(self, game):
        self.visible_reviews = game.debug_reviews() if self.omni else game.review_records(self.seat)
        lines = []
        for seat in range(4) if self.omni else (self.seat,):
            stats = summarize_reviews(r for r in self.visible_reviews if r["player"] == seat)
            rate = "不适用" if stats["optimal_rate"] is None else f"{stats['optimal_rate']:.1%}"
            lines.append(f"玩家 {seat}：已记录 {stats['discards']} 切，强制选择 {stats['forced']} 切；"
                         f"其余 {stats['choices']} 切最优占比 {rate}，增加向听 {stats['shanten_increase_count']} 次；"
                         f"同向听样本 {stats['same_shanten_count']}，进张损失合计 {stats['ukeire_loss_total']} 枚。")
        self.review_summary.set(("本局汇总" if self.data["result"] else "截至当前回放位置的统计") + "\n" + "\n".join(lines))
        self.review_selector["values"] = [f"动作 {r['action_index']} · 玩家 {r['player']} · 切 {Tile(**r['comparison']['selected']['discard']['tile'])}"
                                          for r in self.visible_reviews]
        if self.visible_reviews:
            previous = self._retained_selectors.get("review_selector")
            self.review_selector.current(list(self.review_selector["values"]).index(previous) if previous in self.review_selector["values"] else len(self.visible_reviews) - 1)
        else:
            self.review_selector.set("")
        self.show_review()
        self.tenpai_selector["values"] = self.review_selector["values"]
        if self.visible_reviews:
            previous = self._retained_selectors.get("tenpai_selector")
            self.tenpai_selector.current(list(self.tenpai_selector["values"]).index(previous) if previous in self.tenpai_selector["values"] else len(self.visible_reviews) - 1)
        else:
            self.tenpai_selector.set("")
        self.show_tenpai()

    def show_tenpai(self):
        if not self.visible_reviews:
            self.put(self.tenpai_text, "当前视角尚无弃牌记录。")
            return
        record = self.visible_reviews[self.tenpai_selector.current()]
        analysis = record.get("tenpai")
        if analysis is None:
            self.put(self.tenpai_text, "旧记录未保存听牌分析及必要的自身状态：分析受限，振听未知；不补猜历史。")
            return
        labels = {"discard_furiten": "舍牌振听（限制全部等待）", "temporary_furiten": "临时振听（下次自己摸牌解除）",
                  "riichi_furiten": "立直后振听（本局不解除）", "temporary_furiten_unknown": "临时振听未知",
                  "riichi_furiten_unknown": "立直后振听未知", "permitted": "条件允许", "blocked": "振听禁止荣和",
                  "unknown": "限制未知", "no_yaku": "无役"}
        lines = [f"动作 {record['action_index']} · 玩家 {record['player']} · {analysis['version']}",
                 "分析受限：自身历史状态缺失" if analysis["limited"] else "依据行动前观察投影候选弃牌后的状态",
                 *analysis["assumptions"].values(), ""]
        selected = record["comparison"]["selected"]["discard"]
        for c in analysis["candidates"]:
            d = c["discard"]
            lines.append(f"{'【实际选择】' if d == selected else ''}切 {Tile(**d['tile'])} · "
                         f"{'摸切' if d['is_tsumogiri'] else '手切'} · {c['shanten']} 向听")
            if c["status"] == "not_applicable":
                lines.append("  未听牌：不适用\n")
                continue
            lines.append(f"  牌形等待 {c['shape_wait_types']} 种 / 未见 {c['shape_unseen']} 枚；"
                         f"仍有未见 {c['available_wait_types']} 种；有役未见：荣和 {c['yaku_unseen']['ron']} / 自摸 {c['yaku_unseen']['tsumo']} 枚")
            lines.append("  荣和限制：" + ("、".join(labels[r] for r in c["ron_restrictions"]) or "当前无已知振听限制"))
            lines.append("  仅解除临时振听后（假定等待不变、没有新见逃）：" + labels[c["ron_state_after_own_draw"]])
            for w in c["waits"]:
                lines.append(f"  等待 {w['tile']} · 未见 {w['unseen']} 枚" + ("（已见尽）" if not w['unseen'] else ""))
                for v in w["variants"]:
                    lines.append(f"    {v['tile']}：未见 {v['unseen']} 枚")
                    for kind, name in (("ron", "荣和"), ("tsumo", "自摸")):
                        item, detail = v[kind], ""
                        if item["value"]:
                            value = item["value"]
                            cost = value["cost"]
                            payment = (f"放铳者付 {cost['main']}" if kind == "ron" else
                                       f"每家付 {cost['main']}" if record['player'] == record['observation']['dealer_seat'] else
                                       f"庄家付 {cost['main']}，另两家各付 {cost['additional']}")
                            detail = f"；{value['han']} 番 {value['fu']} 符；{payment}；" + "、".join(y['name'] for y in value['yaku'])
                        lines.append(f"      {name}：{labels[item['permission']]}{detail}")
                        if item["score_status"] != "known":
                            lines.append("      双立直状态未知：上列仅按普通已成立立直条件计算")
            lines.append("")
        self.put(self.tenpai_text, "\n".join(lines))

    def step_review(self, offset):
        if self.visible_reviews:
            self.review_selector.current(max(0, min(len(self.visible_reviews) - 1, self.review_selector.current() + offset)))
            self.show_review()

    def show_review(self):
        self.review_table.delete(*self.review_table.get_children())
        if not self.visible_reviews:
            self.review_description.set("当前视角尚无已完成弃牌的分析记录。")
            return
        record = self.visible_reviews[self.review_selector.current()]
        analysis = record["analysis"]
        hand = record["observation"]["players"][record["player"]]["hand"]
        hand_text = " ".join(str(Tile(**t)) for t in hand["known_tiles"])
        if hand["drawn_tile"]:
            hand_text += "  | 摸入 " + str(Tile(**hand["drawn_tile"]["tile"]))
        self.review_description.set(f"行动前暗手：{hand_text}\n本次：{self.review_comparison(record)}\n"
                                    f"最低向听方案：{analysis['best_shanten']} 向听，最多 {analysis['best_ukeire']} 枚进张。"
                                    f"分析版本：{analysis['version']}\n黄色行为实际选择；立直宣言只评价弃牌，不评价是否立直。")
        for c in analysis["candidates"]:
            chosen = c["discard"] == record["comparison"]["selected"]["discard"]
            self.review_table.insert("", "end", values=(str(Tile(**c["discard"]["tile"])),
                "摸切" if c["discard"]["is_tsumogiri"] else "手切", c["shanten"], c["ukeire"],
                "最优 / 并列" if c["is_best"] else "", "  ".join(f"{t['tile']}×{t['unseen']}" for t in c["effective_tiles"]) or "无"),
                tags=("chosen",) if chosen else ())

    def announce_review(self):
        if self.visible_reviews and self.visible_reviews[-1]["action_index"] == self.game.action_count:
            self.notice.set("切后反馈：" + self.review_comparison(self.visible_reviews[-1]) + "。详见逐切复盘。")

    def toggle_result_details(self):
        if self.result_text.frame.winfo_manager():
            self.result_text.pack_forget()
        else:
            self.result_text.pack(fill="both", expand=True)

    def show_result(self):
        r = self.data["result"]
        self.result_table.delete(*self.result_table.get_children())
        if not r:
            self.result_summary.set("本局尚未结束。结算点数与训练评价分别记录。")
            self.put(self.result_text, "本局尚未结束。此处是麻将点数结算，不是模型训练评分。")
            return
        lines = [RESULT_NAMES[r["kind"]], "", "座位      开局点数      本局变化（含立直支出）      结束点数"]
        for seat in range(4):
            lines.append(f"玩家 {seat}    {r['starting_scores'][seat]}    {r['round_deltas'][seat]:+d}    {r['scores_after'][seat]}")
        lines += [f"供托：{r['riichi_sticks_before']} → {r['riichi_sticks_after']}；归属：{r['sticks_recipient']}",
                  f"{'连庄' if r['dealer_continues'] else '庄家轮换'}；后续庄家 {r['next_dealer']}，本场 {r['next_honba']}",
                  f"和牌 / 满贯玩家：{r['winners']}；流局听牌玩家：{r['tenpai']}"]
        for value in r["values"]:
            yakuman = any(y["yakuman"] for y in value["yaku"])
            score = f"{value['han'] // 13} 倍役满" if yakuman else f"{value['han']} 番 {value['fu']} 符"
            lines += ["", f"玩家 {value['player']}：{score}",
                      "和牌：" + str(Tile(**value["win_tile"])),
                      "暗手：" + " ".join(str(Tile(**t)) for t in value["hand"]),
                      "役 / 宝牌：" + "、".join(f"{YAKU_NAMES.get(y['name'], y['name'])} " +
                                           (f"{y['han'] // 13}倍役满" if y['yakuman'] else f"{y['han']}番") for y in value["yaku"]),
                      "支付（付款座位:点数）：" + str(value["payments"]),
                      "符明细：" + str(value["fu_details"])]
        self.result_summary.set(f"{RESULT_NAMES[r['kind']]}  ·  {'连庄' if r['dealer_continues'] else '庄家轮换'}\n"
                                f"供托 {r['riichi_sticks_before']} → {r['riichi_sticks_after']}  ·  后续庄家 {r['next_dealer']} / {r['next_honba']} 本场")
        for seat in range(4):
            self.result_table.insert("", "end", values=(f"玩家 {seat}", r['starting_scores'][seat], f"{r['round_deltas'][seat]:+d}", r['scores_after'][seat]))
        self.put(self.result_text, "\n".join(lines))

    @staticmethod
    def put(widget, content):
        if widget.get("1.0", "end-1c") == content:
            return
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", content)
        widget.configure(state="disabled")

    def hand_entries(self, player):
        """Sort the visible copy only; retain source indices and a separate drawn slot."""
        hand = player["hand"]
        entries = [(tile, False, i) for i, tile in enumerate(hand["known_tiles"])]
        if self.sort_tiles.get():
            entries.sort(key=lambda entry: ("mpsz".index(entry[0]["suit"]),
                                           entry[0]["rank"], not entry[0]["is_red"]))
        count = len(entries)
        entries.extend((None, False, count + i) for i in range(hand["unknown_count"]))
        if hand["drawn_tile"] is not None:
            entries.append((hand["drawn_tile"]["tile"], True, count + hand["unknown_count"]))
        return entries

    def draw_table(self):
        if not hasattr(self, "data"):
            return
        c = self.canvas
        c.delete("all")
        c.configure(cursor="")
        self.scale = min(max(c.winfo_width(), 1) / 1200, max(c.winfo_height(), 1) / 820)
        self.ox = (c.winfo_width() - 1200 * self.scale) / 2
        self.oy = (c.winfo_height() - 820 * self.scale) / 2
        d = self.data
        self.rect(8, 8, 1192, 812, fill="#194b3d", outline="#427361", width=2)
        self.rect(145, 112, 1055, 666, fill="#205440", outline="#376953")
        self.rect(405, 328, 795, 466, fill="#113e33", outline="#81a58a", width=2)
        # Public count only: decorative segments do not represent physical wall positions.
        self.text(600, 294, "活牌山余量示意 · 非实际位置", 10, fill="#b3c9b6")
        for i in range(35):
            x = 420 + i * 10
            self.rect(x, 309, x + 7, 319, fill="#b9cbb4" if i * 2 < d['remaining_draws'] else "#2c5b49", outline="")
        self.text(600, 346, f"{WINDS[WIND_NAMES.index(d['round_wind'])]} {d['hand_number']} 局  ·  {d['honba']} 本场  ·  供托 {d['riichi_sticks']}", 15)
        self.text(515, 396, f"余牌 {d['remaining_draws']}", 22)
        self.text(515, 431, RESULT_NAMES[d['result']['kind']] if d['result'] else
                  ('响应：玩家 ' if d['phase'] == 'await_response' else '行动：玩家 ') + str(d['actor']), 12)
        self.text(689, 379, "宝牌指示牌", 10)
        for i, tile in enumerate(d["dora_indicators"]):
            self.card(604 + i * 34, 397, tile, 29, 43)
        for relative in range(4):
            seat = (self.seat + relative) % 4
            p = d["players"][seat]
            active = d["actor"] == seat and d["phase"] != "ended"
            title = f"{'▶ ' if active else ''}玩家 {seat} · {WINDS[(seat - d['dealer_seat']) % 4]} · {p['score']}" + (" · 立直" if p['riichi'] else "")
            positions = ((600, 685), (1110, 175), (600, 23), (90, 175))
            title += " · 庄" if seat == d['dealer_seat'] else ""
            px, py = positions[relative]
            self.rect(px - 85, py - 15, px + 85, py + 15, fill="#35614a" if active else "#173e33", outline="#d5b66b" if active else "")
            self.text(px, py, title, 12, fill="#ffe3a5" if active else "#e1ede7")
            entries = self.hand_entries(p)
            step = 54 if relative == 0 else 38
            total_width = len(entries) * step + 14
            for i, (tile, is_drawn, source_index) in enumerate(entries):
                if relative in (0, 2):
                    x, y = 600 - total_width / 2 + i * step + (14 if is_drawn else 0), 715 if relative == 0 else 42
                else:
                    x, y = (1115 if relative == 1 else 45), 207 + i * 32 + (10 if is_drawn else 0)
                key = (seat, source_index, is_drawn)
                clickable = not self.lesson_pending and self.manual_turn() and tile is not None and active and d['phase'] == 'await_discard' and self.preview is None and not self.game.paused
                self.card(x, y, tile, 50 if relative == 0 else 40 if relative in (1, 3) else 34, 70 if relative == 0 else 29 if relative in (1, 3) else 49, key if clickable else None,
                          selected=self.selection is not None and self.selection[0] == key)
                if is_drawn:
                    self.text(x + (58 if relative in (1, 3) else 17), y + (14 if relative in (1, 3) else 78 if relative == 0 else 57), "摸入", 8, fill="#ffd779")
            river_x, river_y = ((486, 512), (838, 322), (486, 140), (162, 322))[relative]
            self.text(river_x + 92, river_y - 15, "牌河", 10, fill="#9fbbb0")
            latest = next((e for e in reversed(d['events']) if e['type'] == 'discard'), None)
            rows = max(1, (len(p['discards']) + 5) // 6)
            river_step = min(45, (150 if relative in (0, 2) else 330) / rows)
            river_height = min(40, river_step - 5)
            for i, discard in enumerate(p["discards"]):
                x, y = river_x + i % 6 * 32, river_y + i // 6 * river_step
                self.card(x, y, discard["tile"], 28, river_height, selected=discard["is_riichi_declaration"])
                if discard["is_riichi_declaration"]:
                    self.text(x + 28, y + river_height, "立", 8, fill="#ffe3a5")
                if latest and latest['player'] == seat and i == len(p['discards']) - 1:
                    self.rect(x - 2, y - 2, x + 30, y + river_height + 2, outline="#e5c975", width=2)
                if discard["claimed_by"] is not None:
                    self.text(x + 14, y + 20, "×", 22, fill="#c52f38")
                    self.text(x + 14, y + river_height - 4, f"→{discard['claimed_by']}", 8, fill="#943b32")
            meld_x, meld_y = ((900, 686), (900, 25), (25, 25), (25, 686))[relative]
            self.text(meld_x, meld_y, "副露：无" if not p["melds"] else "副露", 10, fill="#9fbbb0")
            for j, meld in enumerate(p["melds"]):
                x, y = meld_x + (j % 2) * 133, meld_y + 20 + (j // 2) * 53
                source = next((q['seat'] for q in d['players'] for r in q['discards']
                               if r['id'] == meld['source_discard_id']), None)
                label = ACTION_NAMES[meld['kind']] + (f" ← {source}" if source is not None else "")
                self.text(x + 54, y - 8, label, 9)
                for k, tile in enumerate(meld["tiles"]):
                    self.card(x + k * 28, y, tile, 26, 35)
        self.text(600, 803, "点牌确认弃牌；其他动作从下方菜单选择    |    " + ("庄家人工，其余三家自动" if self.bots_enabled.get() else "四家人工调试"), 11, fill="#b5ccc1")

    def rect(self, x1, y1, x2, y2, **kwargs):
        return self.canvas.create_rectangle(self.ox + x1 * self.scale, self.oy + y1 * self.scale,
                                             self.ox + x2 * self.scale, self.oy + y2 * self.scale, **kwargs)

    def text(self, x, y, value, size, **kwargs):
        kwargs.setdefault("fill", "#f0f4ef")
        return self.canvas.create_text(self.ox + x * self.scale, self.oy + y * self.scale,
                                       text=value, font=("Microsoft YaHei", max(7, int(size * self.scale))), **kwargs)

    def card(self, x, y, tile, w, h, key=None, selected=False):
        tag = f"tile:{key}" if key is not None else ""
        tags = (tag,) if tag else ()
        self.rect(x + 2, y + 3, x + w + 2, y + h + 3, fill="#102e29", outline="", tags=tags)
        face = self.rect(x, y, x + w, y + h, fill="#fff2c7" if selected else "#f6f4e9" if tile else "#497d9b",
                  outline="#f5be4b" if selected else "#c1bba5" if tile else "#76a4bc", width=2 if selected else 1, tags=tags)
        if tile:
            suit, rank, red = tile["suit"], tile["rank"], tile["is_red"]
            color = "#c52f38" if red else {"m": "#7a3030", "p": "#294f85", "s": "#286640", "z": "#263c35"}[suit]
            if suit == "z":
                self.text(x + w / 2, y + h / 2, "東南西北白發中"[rank - 1], h * .43, fill=color, tags=tags)
            elif w > h:
                self.text(x + w / 2, y + h / 2, str(rank) + {"m": "萬", "p": "筒", "s": "索"}[suit],
                          h * .43, fill=color, tags=tags)
            else:
                self.text(x + w / 2, y + h * .32, str(rank), h * .38, fill=color, tags=tags)
                self.text(x + w / 2, y + h * .76, {"m": "萬", "p": "筒", "s": "索"}[suit], h * .23, fill=color, tags=tags)
            if red:
                self.rect(x + 3, y + 3, x + 6, y + 6, fill="#c52f38", outline="", tags=tags)
        else:
            self.rect(x + 5, y + 6, x + w - 5, y + h - 6, outline="#84abc0", tags=tags)
        if key is not None:
            self.canvas.tag_bind(tag, "<Enter>", lambda event, item=face: (self.canvas.itemconfigure(item, outline="#eccb76", width=2), self.canvas.configure(cursor="hand2")))
            self.canvas.tag_bind(tag, "<Leave>", lambda event, item=face, chosen=selected: (self.canvas.itemconfigure(item, outline="#f5be4b" if chosen else "#c1bba5", width=2 if chosen else 1), self.canvas.configure(cursor="")))
            self.canvas.tag_bind(tag, "<Button-1>", lambda event, k=key, t=tile: self.select_tile(k, t))

    def select_tile(self, key, tile):
        if (self.lesson_pending or not self.manual_turn() or self.preview is not None or self.game.paused or self.data['phase'] != 'await_discard' or key[0] != self.data["actor"]
                or (not self.omni and self.seat != key[0])):
            return
        self.selection = (key, tile)
        self.submit_button.configure(state="normal")
        self.notice.set(f"已选择 {Tile(**tile)}（{'摸切' if key[2] else '手切'}），点击确认弃牌。")
        self.draw_table()

    def submit(self):
        if self.lesson_pending or self.preview is not None or self.game.paused or self.selection is None or not self.manual_turn():
            return
        key, tile = self.selection
        try:
            action = Discard(key[0], Tile(**tile), key[2])
            self.game.submit(action, mode=self.submission_mode())
            self.begin_lesson(action)
            self.dirty = True
            self.refresh()
            self.announce_review()
        except ValueError as exc:
            self.dirty = self.dirty or self.game.paused
            self.refresh()
            self.notice.set(str(exc))

    def register_action(self, name, builder):
        """builder(window) may collect parameters; return a core action or None."""
        self.action_builders[name] = builder
        self.special_box["values"] = list(dict.fromkeys((*self.special_box["values"], name)))

    def submit_special(self):
        name = self.special.get()
        if self.lesson_pending or not self.manual_turn() or self.preview is not None or self.game.paused:
            self.notice.set("讲解停顿、机器人回合、回放或暂停中不能执行人工动作。")
            return
        if name not in self.action_builders and name not in self.decision_options:
            self.notice.set("当前没有可执行的所选动作。")
            return
        if not self.omni and self.seat != self.data["actor"]:
            self.notice.set("请切换到行动玩家。")
            return
        try:
            action = self.action_builders[name](self) if name in self.action_builders else self.decision_options[name]
            if action is not None:
                self.game.submit(action, mode=self.submission_mode())
                self.begin_lesson(action)
                self.dirty = True
                self.refresh()
                self.announce_review()
                if self.data['result']:
                    self.tabs.select(self.result_frame)
        except ValueError as exc:
            self.dirty = self.dirty or self.game.paused
            self.refresh()
            self.notice.set(str(exc))

    def follow_actor(self):
        self.view.set(f"玩家 {self.human_seat if self.bots_enabled.get() else self.data['actor']}")
        self.refresh()

    def replace_ok(self):
        return not self.dirty or messagebox.askyesno("未保存", "放弃未保存的牌局修改？", parent=self.root)

    def new_game(self):
        if not self.replace_ok():
            return
        seed = simpledialog.askinteger("新局", "输入复现种子（环境控制，不提供给玩家观察）：", parent=self.root, initialvalue=0)
        if seed is not None:
            self.start_game(seed)

    def random_game(self):
        if self.replace_ok():
            self.start_game(None)

    def start_game(self, seed):
        self.game = Game(seed, rules=Rules(self.aka_enabled.get(), self.kan_enabled.get()),
                         round_wind=WIND_NAMES[WINDS.index(self.round_wind.get())])
        self.human_seat = self.game.observe(0)["dealer_seat"]
        self.view.set(f"玩家 {self.human_seat}")
        self.preview, self.dirty = None, True
        self.refresh()
        self.tabs.select(self.training_frame)

    def load(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("游戏存档", "*.json")])
        if path and self.replace_ok():
            try:
                game = Game.load(path)
                self.game, self.preview, self.dirty = game, None, False
                self.human_seat = game.observe(0)["dealer_seat"]
                self.view.set(f"玩家 {self.human_seat}")
                if self.bots_enabled.get():
                    game.stop()  # Inspect a loaded checkpoint before explicitly resuming bots.
                self.refresh()
            except (ValueError, OSError) as exc:
                self.notice.set(str(exc))

    def save(self):
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json", initialfile="game.json")
        if path:
            try:
                self.game.save(path)
                self.dirty = False
                self.refresh()
                self.notice.set("已保存当前完整游戏存档（包含隐藏信息，不是玩家观察导出）。")
            except OSError as exc:
                self.notice.set(str(exc))

    def pause(self):
        self.game.resume() if self.game.paused else self.game.stop()
        self.dirty = True
        self.refresh()

    def undo(self):
        try:
            if self.preview is not None:
                raise ValueError("请先返回当前牌局。")
            self.game.undo()
            self.lesson_pending = None
            if self.bots_enabled.get():
                self.game.stop()
            self.dirty = True
            self.refresh()
        except ValueError as exc:
            self.notice.set(str(exc))

    def show_history(self):
        self.preview = int(self.history.get())
        self.refresh()

    def live(self):
        self.preview = None
        self.refresh()

    def rewind(self):
        count = int(self.history.get())
        if count < self.game.action_count and messagebox.askyesno("回退", f"删除第 {count} 次动作后的操作？", parent=self.root):
            self.game.rewind(count)
            self.lesson_pending = None
            if self.bots_enabled.get():
                self.game.stop()
            self.preview, self.dirty = None, True
            self.refresh()

    def run_tests(self):
        if self.testing:
            return
        self.testing = True
        self.test_button.configure(state="disabled")
        self.open_page(self.test_frame)
        self.put(self.test_text, "正在运行全部自动化测试…")

        def worker():
            try:
                result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                                        cwd=PROJECT, capture_output=True, text=True, encoding="utf-8",
                                        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                self.queue.put(result.stdout + result.stderr + f"\n退出码：{result.returncode}")
            except OSError as exc:
                self.queue.put(str(exc))
        Thread(target=worker, daemon=True).start()

    def manual_turn(self):
        return not self.bots_enabled.get() or self.data["actor"] == self.human_seat

    def advance_bot(self):
        # Tcl-owned combobox popdowns have no Python widget wrapper. Only ask
        # whether a grab exists, without grab_current() resolving that wrapper.
        if (self.lesson_pending or self.preview is not None or self.game.paused or self.testing
                or self.root.tk.call("grab", "current", self.root._w)):
            return
        view = self.game.observe(self.human_seat)
        actor = view["actor"]
        if view["result"]:
            return
        try:
            legal = self.game.legal_actions(actor)
            manual = not self.bots_enabled.get() or actor == self.human_seat
            if manual and self.auto_pass.get() and view["phase"] == "await_response" and len(legal) == 1 and legal[0] == Decision(actor, "pass"):
                self.game.submit(legal[0], policy="ui-auto-pass-v1", mode=self.submission_mode())
                self.dirty = True
                self.refresh()
                return
            if manual:
                return
            yaku = self.bot_strategy.get() != "基础牌效"
            chooser = choose_riichi_action if self.bot_strategy.get() == "基础立直" else choose_yaku_action if yaku else choose_action
            mode = "fold" if self.bot_mode.get() == "弃和" else "attack"
            args = (self.game.observe(actor), legal)
            action = chooser(*args, mode=mode) if yaku else chooser(*args)
            self.game.submit(action, policy=RIICHI_VERSION if self.bot_strategy.get() == "基础立直" else STRATEGY_VERSION if yaku else "basic-efficiency-v1", mode=mode if yaku else None)
            self.dirty = True
            self.refresh()
            if self.data["result"] and self.tabs.select() == str(self.training_frame):
                self.tabs.select(self.result_frame)
        except ValueError as exc:
            self.game.stop()
            self.dirty = True
            self.refresh()
            self.notice.set(f"机器人已暂停：{exc}")

    def poll(self):
        # Keep polling scheduled even if a callback raises and Tk reports it.
        self.poll_id = self.root.after(100, self.poll)
        try:
            self.put(self.test_text, self.queue.get_nowait())
            self.testing = False
            self.test_button.configure(state="normal")
            if self.lesson_pending:
                self.lesson_button.configure(state="disabled" if self.game.paused or self.preview is not None else "normal")
        except Empty:
            pass
        if monotonic() >= self.next_bot_time:
            self.advance_bot()
            self.next_bot_time = monotonic() + 0.4

    def close(self):
        if self.replace_ok():
            self.root.after_cancel(self.poll_id)
            self.root.destroy()


def main():
    root = tk.Tk()
    GameWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
