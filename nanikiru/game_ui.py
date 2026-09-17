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
else:
    from .bot import choose_action, choose_yaku_action
    from .game import Game
    from .models import Decision, Discard, Tile
    from .scoring import Rules, WINDS as WIND_NAMES
    from .efficiency import summarize_reviews
    from .decision import STRATEGY_VERSION


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
        toolbar = ttk.Frame(root, padding=6)
        toolbar.pack(fill="x")
        for title, callback in (("随机新局", self.random_game), ("按种子开局", self.new_game), ("加载游戏", self.load),
                                ("保存游戏（全知）", self.save), ("暂停 / 恢复", self.pause),
                                ("撤销动作", self.undo)):
            ttk.Button(toolbar, text=title, command=callback).pack(side="left", padx=2)
        toolbar = ttk.Frame(root, padding=(6, 0, 6, 4))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="视角：").pack(side="left", padx=(12, 0))
        box = ttk.Combobox(toolbar, textvariable=self.view, width=13, state="readonly",
                           values=[f"玩家 {i}" for i in range(4)] + ["全知调试"])
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda event: self.refresh())
        ttk.Checkbutton(toolbar, text="自动理牌", variable=self.sort_tiles,
                        command=self.refresh).pack(side="left", padx=8)
        self.aka_enabled = tk.BooleanVar(value=self.game.rules.aka_dora_enabled)
        self.kan_enabled = tk.BooleanVar(value=self.game.rules.kan_dora_enabled)
        ttk.Checkbutton(toolbar, text="新局计赤宝", variable=self.aka_enabled).pack(side="left")
        ttk.Checkbutton(toolbar, text="新局开杠宝", variable=self.kan_enabled).pack(side="left")
        self.round_wind = tk.StringVar(value=WINDS[WIND_NAMES.index(self.game.debug_state().round_wind)])
        ttk.Label(toolbar, text="新局场风：").pack(side="left")
        ttk.Combobox(toolbar, textvariable=self.round_wind, values=WINDS, width=3, state="readonly").pack(side="left")
        ttk.Checkbutton(toolbar, text="三家机器人", variable=self.bots_enabled,
                        command=self.refresh).pack(side="left", padx=8)
        self.bot_strategy = tk.StringVar(value="役种感知")
        strategy_box = ttk.Combobox(toolbar, textvariable=self.bot_strategy, values=("役种感知", "基础牌效"),
                     state="readonly", width=8)
        strategy_box.pack(side="left")
        strategy_box.bind("<<ComboboxSelected>>", lambda event: self.update_mode_controls())
        modes = ttk.Frame(root, padding=(12, 0, 6, 4))
        modes.pack(fill="x")
        self.human_mode = tk.StringVar(value="进攻")
        self.bot_mode = tk.StringVar(value="进攻")
        ttk.Label(modes, text="人工复盘模式：").pack(side="left")
        ttk.Combobox(modes, textvariable=self.human_mode, values=("进攻", "弃和"), state="readonly", width=6).pack(side="left")
        ttk.Label(modes, text="  三家役种机器人模式：").pack(side="left")
        self.bot_mode_box = ttk.Combobox(modes, textvariable=self.bot_mode, values=("进攻", "弃和"), state="readonly", width=6)
        self.bot_mode_box.pack(side="left")
        self.mode_hint = ttk.Label(modes, text="仅影响后续决策；人工模式不代替操作")
        self.mode_hint.pack(side="left", padx=8)
        self.banner = tk.Label(root, anchor="w", padx=10, pady=5)
        self.banner.pack(fill="x")
        self.tabs = ttk.Notebook(root)
        self.canvas = tk.Canvas(self.tabs, bg="#183f36", highlightthickness=0)
        self.event_text = ScrolledText(self.tabs, wrap="word", font=("Microsoft YaHei", 11))
        self.json_text = ScrolledText(self.tabs, wrap="none")
        self.test_text = ScrolledText(self.tabs, wrap="word")
        self.result_text = ScrolledText(self.tabs, wrap="word", font=("Microsoft YaHei", 11))
        self.decision_frame = ttk.Frame(self.tabs, padding=8)
        self.decision_selector = ttk.Combobox(self.decision_frame, state="readonly", width=65)
        self.decision_selector.pack(fill="x")
        self.decision_selector.bind("<<ComboboxSelected>>", lambda event: self.show_decision())
        self.decision_text = ScrolledText(self.decision_frame, wrap="word", font=("Microsoft YaHei", 11))
        self.decision_text.pack(fill="both", expand=True)
        self.review_frame = ttk.Frame(self.tabs, padding=8)
        for widget, title in ((self.canvas, "牌桌"), (self.event_text, "当前视角事件"),
                              (self.json_text, "当前视角数据"), (self.result_text, "单局结算"),
                              (self.review_frame, "逐切复盘"), (self.decision_frame, "役种决策复盘"), (self.test_text, "测试输出")):
            self.tabs.add(widget, text=title)
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
        self.canvas.bind("<Configure>", lambda event: self.draw_table())
        controls = ttk.Frame(root, padding=6)
        controls.pack(side="bottom", fill="x")
        self.submit_button = ttk.Button(controls, text="确认弃牌", command=self.submit)
        self.submit_button.pack(side="left", padx=3)
        self.view_button = ttk.Button(controls, text="返回庄家视角", command=self.follow_actor)
        self.view_button.pack(side="left", padx=3)
        self.special = tk.StringVar(value="吃")
        self.special_box = ttk.Combobox(controls, state="readonly", textvariable=self.special, width=29,
                                       values=("吃", "碰", "明杠", "暗杠", "加杠", "立直", "自摸", "荣和", "流局"))
        self.special_box.pack(side="left", padx=3)
        ttk.Button(controls, text="执行所选动作", command=self.submit_special).pack(side="left", padx=3)
        self.test_button = ttk.Button(controls, text="运行自动化测试", command=self.run_tests)
        self.test_button.pack(side="right")
        ttk.Label(root, textvariable=self.notice, padding=(10, 3)).pack(side="bottom", fill="x")
        history = ttk.Frame(root, padding=6)
        history.pack(side="bottom", fill="x")
        ttk.Label(history, text="已执行动作数（含过）：").pack(side="left")
        self.history = ttk.Combobox(history, state="readonly", width=8)
        self.history.pack(side="left")
        for title, callback in (("只读回放", self.show_history), ("返回当前", self.live), ("回退重录", self.rewind)):
            ttk.Button(history, text=title, command=callback).pack(side="left", padx=3)
        self.tabs.pack(fill="both", expand=True, padx=6)
        self.refresh()
        self.poll_id = root.after(100, self.poll)

    def update_mode_controls(self):
        basic = self.bot_strategy.get() == "基础牌效"
        self.bot_mode_box.configure(state="disabled" if basic else "readonly")
        self.mode_hint.configure(text="基础牌效机器人不应用攻守模式" if basic else "仅影响后续决策；人工模式不代替操作")

    def submission_mode(self):
        return "fold" if self.human_mode.get() == "弃和" else "attack"

    def visible_game(self):
        return self.game if self.preview is None else self.game.replay(self.preview)

    def refresh(self):
        self.selection = None
        game = self.visible_game()
        self.omni = self.view.get() == "全知调试"
        self.seat = 0 if self.omni else int(self.view.get()[-1])
        self.data = game.debug_view() if self.omni else game.observe(self.seat)
        mode = "全知调试 · 包含四家暗手与未公开牌山，禁止作为玩家输入" if self.omni else f"玩家 {self.seat} 视角 · 其余暗手已隐藏"
        self.banner.configure(text=mode + (f" | 人工：庄家 {self.human_seat}；三家机器人" if self.bots_enabled.get() else " | 全人工调试") + (f" | 只读回放 {self.preview} 手" if self.preview is not None else "")
                              + (" | 已暂停" if self.game.paused else "")
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
        if self.preview is None and self.manual_turn() and (self.omni or self.seat == self.data["actor"]):
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
        self.draw_table()

    def show_decisions(self, game):
        self.visible_decisions = game.debug_decisions() if self.omni else game.decision_records(self.seat)
        self.decision_selector["values"] = [f"动作 {r['action_index']} · 玩家 {r['player']} · {r['actual_action'].get('kind', '弃牌')}"
                                              for r in self.visible_decisions]
        if self.visible_decisions:
            self.decision_selector.current(len(self.visible_decisions) - 1)
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
            self.review_selector.current(len(self.visible_reviews) - 1)
        else:
            self.review_selector.set("")
        self.show_review()

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

    def show_result(self):
        r = self.data["result"]
        if not r:
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
        self.put(self.result_text, "\n".join(lines))

    @staticmethod
    def put(widget, content):
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
        self.scale = min(max(c.winfo_width(), 1) / 1200, max(c.winfo_height(), 1) / 820)
        self.ox = (c.winfo_width() - 1200 * self.scale) / 2
        self.oy = (c.winfo_height() - 820 * self.scale) / 2
        d = self.data
        self.rect(415, 332, 785, 457, fill="#245348", outline="#618478")
        self.text(600, 357, f"{WINDS[WIND_NAMES.index(d['round_wind'])]} {d['hand_number']} 局  ·  {d['honba']} 本场  ·  供托 {d['riichi_sticks']}", 15)
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
            positions = ((600, 683), (1115, 163), (600, 17), (87, 163))
            self.text(*positions[relative], title, 13, fill="#ffd779" if active else "#e1ede7")
            entries = self.hand_entries(p)
            total_width = len(entries) * 38 + 14
            for i, (tile, is_drawn, source_index) in enumerate(entries):
                if relative in (0, 2):
                    x, y = 600 - total_width / 2 + i * 38 + (14 if is_drawn else 0), 715 if relative == 0 else 42
                else:
                    x, y = (1090 if relative == 1 else 25) + (i % 2) * 40, 207 + (i // 2) * 57 + (8 if is_drawn else 0)
                key = (seat, source_index, is_drawn)
                clickable = self.manual_turn() and tile is not None and active and d['phase'] == 'await_discard' and self.preview is None and not self.game.paused
                self.card(x, y, tile, 34, 49, key if clickable else None,
                          selected=self.selection is not None and self.selection[0] == key)
                if is_drawn:
                    self.text(x + 17, y + 57, "摸入", 8, fill="#ffd779")
            river_x, river_y = ((486, 512), (838, 322), (486, 140), (162, 322))[relative]
            self.text(river_x + 92, river_y - 15, "牌河", 10, fill="#9fbbb0")
            for i, discard in enumerate(p["discards"]):
                x, y = river_x + i % 6 * 32, river_y + i // 6 * 45
                self.card(x, y, discard["tile"], 28, 40, selected=discard["is_riichi_declaration"])
                if discard["claimed_by"] is not None:
                    self.text(x + 14, y + 20, "×", 22, fill="#c52f38")
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
        self.rect(x, y, x + w, y + h, fill="#fff2c7" if selected else "#f6f4e9" if tile else "#497d9b",
                  outline="#f5be4b" if selected else "#c1bba5" if tile else "#76a4bc", width=2 if selected else 1, tags=tags)
        if tile:
            suit, rank, red = tile["suit"], tile["rank"], tile["is_red"]
            color = "#c52f38" if red else {"m": "#7a3030", "p": "#294f85", "s": "#286640", "z": "#263c35"}[suit]
            if suit == "z":
                self.text(x + w / 2, y + h / 2, "東南西北白發中"[rank - 1], h * .43, fill=color, tags=tags)
            else:
                self.text(x + w / 2, y + h * .32, str(rank), h * .38, fill=color, tags=tags)
                self.text(x + w / 2, y + h * .76, {"m": "萬", "p": "筒", "s": "索"}[suit], h * .23, fill=color, tags=tags)
            if red:
                self.rect(x + 3, y + 3, x + 6, y + 6, fill="#c52f38", outline="", tags=tags)
        else:
            self.rect(x + 5, y + 6, x + w - 5, y + h - 6, outline="#84abc0", tags=tags)
        if key is not None:
            self.canvas.tag_bind(tag, "<Button-1>", lambda event, k=key, t=tile: self.select_tile(k, t))

    def select_tile(self, key, tile):
        if (not self.manual_turn() or self.preview is not None or self.game.paused or self.data['phase'] != 'await_discard' or key[0] != self.data["actor"]
                or (not self.omni and self.seat != key[0])):
            return
        self.selection = (key, tile)
        self.submit_button.configure(state="normal")
        self.notice.set(f"已选择 {Tile(**tile)}（{'摸切' if key[2] else '手切'}），点击确认弃牌。")
        self.draw_table()

    def submit(self):
        if self.selection is None or not self.manual_turn():
            return
        key, tile = self.selection
        try:
            self.game.submit(Discard(key[0], Tile(**tile), key[2]), mode=self.submission_mode())
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
        if not self.manual_turn() or self.preview is not None or self.game.paused:
            self.notice.set("机器人回合、回放或暂停中不能执行人工动作。")
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
                self.dirty = True
                self.refresh()
                self.announce_review()
                if self.data['result']:
                    self.tabs.select(self.result_text)
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
        self.tabs.select(self.canvas)

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
            if self.bots_enabled.get():
                self.game.stop()
            self.preview, self.dirty = None, True
            self.refresh()

    def run_tests(self):
        if self.testing:
            return
        self.testing = True
        self.test_button.configure(state="disabled")
        self.tabs.select(self.test_text)
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
        if (not self.bots_enabled.get() or self.preview is not None or self.game.paused or self.testing
                or self.root.tk.call("grab", "current", self.root._w)):
            return
        view = self.game.observe(self.human_seat)
        actor = view["actor"]
        if view["result"] or actor == self.human_seat:
            return
        try:
            yaku = self.bot_strategy.get() == "役种感知"
            chooser = choose_yaku_action if yaku else choose_action
            mode = "fold" if self.bot_mode.get() == "弃和" else "attack"
            args = (self.game.observe(actor), self.game.legal_actions(actor))
            action = chooser(*args, mode=mode) if yaku else chooser(*args)
            self.game.submit(action, policy=STRATEGY_VERSION if yaku else "basic-efficiency-v1", mode=mode if yaku else None)
            self.dirty = True
            self.refresh()
            if self.data["result"]:
                self.tabs.select(self.result_text)
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
