"""Single-window sandbox: python -m test_ui."""

import tkinter as tk
from collections import Counter
from tkinter import ttk

from nanikiru.analysis import analyze_discard_shapes
from nanikiru.analysis.minimal_policy import rank_discard_candidates
from nanikiru.models import UNKNOWN
from nanikiru.tiles import physical_capacity
from .positions import TILE_CODES, make_position, random_position, replace_tile, sorted_hand
from .tiles import TileButton, tile_name

BG, PANEL, TEXT, MUTED = "#edf2ef", "#ffffff", "#243b35", "#62766d"
TABLE = "#244f45"


def label(parent, text, *, size=11, color=TEXT, bold=False, **options):
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                    font=("Microsoft YaHei", size, "bold" if bold else "normal"), **options)


def clear(frame):
    for child in frame.winfo_children():
        child.destroy()


def scroll_body(parent):
    frame = tk.Frame(parent, bg=PANEL)
    frame.pack(fill="both", expand=True)
    canvas = tk.Canvas(frame, bg=PANEL, highlightthickness=0, height=210)
    bar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
    bar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    canvas.configure(yscrollcommand=bar.set)
    body = tk.Frame(canvas, bg=PANEL)
    window = canvas.create_window(0, 0, anchor="nw", window=body)
    body.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
    return body


class PositionApp:
    def __init__(self, root, position=None):
        self.root = root
        root.title("Nanikiru · 单局面牌效测试")
        root.configure(bg=BG)
        root.geometry(f"1160x{min(820, root.winfo_screenheight()-90)}")
        root.minsize(1100, 650)
        root.option_add("*Font", ("Microsoft YaHei", 10))
        outer = tk.Frame(root, bg=BG)
        outer.pack(fill="both", expand=True, padx=24, pady=18)

        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x")
        label(header, "NANIKIRU  /  牌效实验", size=22, bold=True).pack(side="left")
        ttk.Button(header, text="随机换一手", command=self.new_position).pack(side="right", padx=(10, 0))
        ttk.Button(header, text="载入听牌示例", command=self.example).pack(side="right")
        self.editing = tk.BooleanVar(value=False)
        ttk.Checkbutton(header, text="自行组牌", variable=self.editing, command=self.toggle_editor).pack(side="right", padx=10)
        label(outer, "单局面 · 门清 / 未立直 / 普通摸牌后    |    点击手牌或摸牌，下方分区同步显示结果",
              color=MUTED, anchor="w").pack(fill="x", pady=(6, 16))

        table = tk.Frame(outer, bg=TABLE, padx=15, pady=15)
        table.pack(fill="x")
        hand_area = tk.Frame(table, bg=TABLE)
        hand_area.pack(side="left", anchor="n")
        label(hand_area, "手牌 · 13 张 / 已理牌", color="#e4eee7", bold=True, anchor="w").pack(fill="x", pady=(0, 8))
        self.hand_frame = tk.Frame(hand_area, bg=TABLE)
        self.hand_frame.pack()
        draw_area = tk.Frame(table, bg=TABLE)
        draw_area.pack(side="left", anchor="n", padx=(18, 14))
        label(draw_area, "摸进", color="#edc776", bold=True).pack(pady=(0, 8))
        self.draw_frame = tk.Frame(draw_area, bg=TABLE)
        self.draw_frame.pack()
        indicator_area = tk.Frame(table, bg=TABLE)
        indicator_area.pack(side="right", anchor="n")
        label(indicator_area, "宝牌指示牌", color="#e4eee7", bold=True).pack(pady=(0, 8))
        self.indicator_frame = tk.Frame(indicator_area, bg=TABLE)
        self.indicator_frame.pack()

        label(outer, "金色边框 = 当前选择；这里只预览弃牌，不推进牌局。宝牌指示牌计入已见库存。",
              color=MUTED, size=10, anchor="w").pack(fill="x", pady=(8, 14))
        self.editor = tk.Frame(outer, bg=PANEL, padx=12, pady=5)
        self.editor_text = tk.StringVar()
        tk.Label(self.editor, textvariable=self.editor_text, bg=PANEL, fg=TEXT,
                 anchor="w", font=("Microsoft YaHei", 10)).pack(fill="x")
        palette = tk.Frame(self.editor, bg=PANEL)
        palette.pack(anchor="w")
        self.palette_buttons = {}
        for i, code in enumerate(TILE_CODES):
            button = TileButton(palette, code, lambda code=code: self.edit_tile(code), small=True)
            button.grid(row=i // 19, column=i % 19, padx=3)
            self.palette_buttons[code] = button
        lower = tk.Frame(outer, bg=BG)
        self.lower = lower
        lower.pack(fill="both", expand=True)
        lower.columnconfigure(0, weight=3, minsize=580)
        lower.columnconfigure(1, weight=2, minsize=430)
        lower.rowconfigure(0, weight=1)
        left = tk.Frame(lower, bg=PANEL, padx=16, pady=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = tk.Frame(lower, bg=PANEL, padx=16, pady=14)
        right.grid(row=0, column=1, sticky="nsew")
        label(left, "01  所选弃牌分析", size=15, bold=True, anchor="w").pack(fill="x")
        self.selection_text = tk.StringVar()
        tk.Label(left, textvariable=self.selection_text, bg=PANEL, fg=TEXT,
                 font=("Microsoft YaHei", 15, "bold"), anchor="w").pack(fill="x", pady=(14, 6))
        label(left, "进张：能降低向听数的牌；0 向听时为和牌形等待。", size=10, color=MUTED, anchor="w").pack(fill="x")
        self.effective_frame = scroll_body(left)

        label(right, "02  最小牌效推荐", size=15, bold=True, anchor="w").pack(fill="x")
        label(right, "先选最小向听，再选最大进张；相同则并列。", size=10, color=MUTED, anchor="w").pack(fill="x", pady=(6, 10))
        self.recommendation_text = tk.StringVar()
        tk.Label(right, textvariable=self.recommendation_text, bg=PANEL, fg=TEXT,
                 font=("Microsoft YaHei", 12, "bold"), anchor="w").pack(fill="x", pady=(0, 8))
        self.recommendation_frame = scroll_body(right)
        label(right, "可点击推荐牌，查看左侧分析。\n相同牌码合并展示，赤牌单独显示。", size=10,
              color=MUTED, justify="left", anchor="w").pack(fill="x", pady=(8, 0))
        label(outer, "测试假设：除手牌和 1 张宝牌指示牌外，无其他已公开牌。未见进张数 ≠ 牌山剩余数。\n"
              "推荐仅为一步牌效偏好，不评估打点、役、攻守或和牌许可；不恢复旧对局/UI 运行链。",
              size=10, color=MUTED, justify="left", anchor="w").pack(fill="x", pady=(12, 0))
        self.new_position(position)

    def example(self):
        self.new_position(make_position("1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s".split(), "9m", "6s"))

    def new_position(self, position=None):
        self.position = random_position() if position is None else position
        self.by_id = {t.id: t for t in self.position.tiles}
        self.draw_id = self.position.draw_context["tile_id"]
        self.hand = sorted_hand(self.position)
        ids = tuple(t.id for t in self.hand) + (self.draw_id,)
        self.candidates = analyze_discard_shapes(self.position, ids)
        self.results = {c.action.tile_ids[0]: c for c in self.candidates}
        self.ranking = rank_discard_candidates(self.candidates)
        self.tile_buttons = {}
        for frame in (self.hand_frame, self.draw_frame, self.indicator_frame, self.recommendation_frame):
            clear(frame)
        for tile_id in ids:
            frame = self.draw_frame if tile_id == self.draw_id else self.hand_frame
            button = TileButton(frame, self.by_id[tile_id].code, lambda tid=tile_id: self.select(tid))
            button.pack(side="left")
            self.tile_buttons[tile_id] = button
        indicator = next(t for t in self.position.tiles if t.zone == "dora")
        self.indicator_button = TileButton(self.indicator_frame, indicator.code, lambda: self.choose_indicator(indicator.id))
        self.indicator_button.pack()
        self.show_recommendations()
        self.select(self.draw_id)

    def select(self, tile_id):
        self.selected_id = tile_id
        self.edit_target = tile_id
        self.indicator_button.select(False)
        for tid, button in self.tile_buttons.items():
            button.select(tid == tile_id)
        result = self.results[tile_id]
        shanten = result.metrics["shanten"]
        ukeire = result.metrics["ukeire"]
        shape = "未知向听" if shanten.value is None else ("听牌 · 0 向听" if shanten.value == 0 else f"{shanten.value} 向听")
        count = "未知" if ukeire.value is None else str(ukeire.value)
        self.selection_text.set(f"切 {tile_name(self.by_id[tile_id].code)}    {shape}    进张 {count} 枚")
        self.refresh_editor()
        clear(self.effective_frame)
        effective = result.metrics["effective_tiles"].value
        if not effective:
            label(self.effective_frame, "暂无可计算的进张" if effective is None else "没有未见进张", color=MUTED).pack(pady=20)
            return
        for i, item in enumerate(effective):
            cell = tk.Frame(self.effective_frame, bg=PANEL)
            cell.grid(row=i // 8, column=i % 8, padx=4, pady=7)
            TileButton(cell, item["tile"], small=True).pack()
            unseen = "?" if item["unseen"] is UNKNOWN else item["unseen"]
            label(cell, f"{unseen} 枚", size=10, color=MUTED).pack()

    def show_recommendations(self):
        if self.ranking.value is None:
            self.recommendation_text.set("信息不足，暂无推荐")
            return
        choices = self.ranking.value[0]
        first = self.results[choices[0].tile_ids[0]]
        shanten, ukeire = first.metrics["shanten"].value, first.metrics["ukeire"].value
        self.recommendation_text.set(f"最优：{shanten} 向听 / 进张 {ukeire if ukeire is not None else '?'} 枚")
        groups = {}
        for action in choices:
            tile_id = action.tile_ids[0]
            groups.setdefault(self.by_id[tile_id].code, []).append(tile_id)
        for i, (code, ids) in enumerate(groups.items()):
            cell = tk.Frame(self.recommendation_frame, bg=PANEL)
            cell.grid(row=i // 5, column=i % 5, padx=5, pady=7)
            TileButton(cell, code, lambda tid=ids[0]: self.select(tid)).pack()
            label(cell, tile_name(code), size=10).pack()
            label(cell, f"可选 {len(ids)} 张", size=9, color=MUTED).pack()

    def toggle_editor(self):
        if self.editing.get():
            self.editor.pack(fill="x", before=self.lower, pady=(0, 10))
            # Leave room for both results while the two-row palette is visible.
            height = min(900, self.root.winfo_screenheight()-90)
            self.root.geometry(f"{self.root.winfo_width()}x{height}")
        else:
            self.editor.pack_forget()
            self.select(self.selected_id)
        self.refresh_editor()

    def choose_indicator(self, tile_id):
        if self.editing.get():
            self.edit_target = tile_id
            for button in self.tile_buttons.values():
                button.select(False)
            self.indicator_button.select(True)
            self.refresh_editor()

    def refresh_editor(self):
        target = self.by_id[self.edit_target]
        location = "宝牌指示牌" if target.zone == "dora" else "摸进牌" if target.id == self.draw_id else "手牌"
        self.editor_text.set(f"正在替换：{location} · {tile_name(target.code)}。先点上方位置，再点下方牌种；灰色表示库存不足。")
        used = Counter(t.code for t in self.position.tiles if t.id != self.edit_target)
        for code, button in self.palette_buttons.items():
            button.enable(used[code] < physical_capacity(code))

    def edit_tile(self, code):
        if not self.editing.get() or not self.palette_buttons[code].enabled:
            return
        target = self.edit_target
        selected = self.selected_id
        position = replace_tile(self.position, target, code)
        self.new_position(position)
        self.select(target if target != "indicator" else selected)
        if target == "indicator":
            self.choose_indicator(target)


def main():
    root = tk.Tk()
    PositionApp(root)
    root.mainloop()
