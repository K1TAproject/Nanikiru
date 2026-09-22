"""Read-only pair view of saved analyses; contains no Mahjong rule calculations."""

import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from .models import Tile
from .tenpai_comparison import compare_candidates

LABELS = {'permitted': '条件允许', 'blocked': '振听禁止荣和', 'unknown': '限制未知', 'no_yaku': '无役',
          'discard_furiten': '舍牌振听（全部等待）', 'temporary_furiten': '临时振听（下次自己摸牌解除）',
          'riichi_furiten': '立直后振听（本局不解除）', 'temporary_furiten_unknown': '临时振听未知',
          'riichi_furiten_unknown': '立直后振听未知'}


class ComparisonPanel(ttk.Frame):
    def __init__(self, parent, on_preview=None):
        super().__init__(parent, padding=8)
        controls = ttk.Frame(self)
        controls.pack(fill='x')
        self.record_box = ttk.Combobox(controls, state='readonly', width=32)
        self.record_box.pack(side='left', padx=(0, 8))
        self.record_box.bind('<<ComboboxSelected>>', lambda e: self.select_record())
        ttk.Label(controls, text='对照弃牌').pack(side='left')
        self.candidate_box = ttk.Combobox(controls, state='readonly', width=18)
        self.candidate_box.pack(side='left', padx=6)
        self.candidate_box.bind('<<ComboboxSelected>>', lambda e: self.render())
        self.details = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text='番符与条件明细', variable=self.details, command=self.render).pack(side='left')
        if on_preview is not None:
            ttk.Button(controls, text='推进／立直', command=on_preview).pack(side='left', padx=6)
        self.summary = ScrolledText(self, height=4, wrap='word', font=('Microsoft YaHei', 10))
        self.summary.pack(fill='x', pady=8)
        columns = ttk.Frame(self)
        columns.pack(fill='both', expand=True)
        columns.columnconfigure((0, 1), weight=1, uniform='candidate')
        columns.rowconfigure(0, weight=1)
        self.canvases, self.texts = [], []
        for i, label in enumerate(('实际选择 · 行动前投影', '对照候选 · 不改变真实局面')):
            frame = ttk.LabelFrame(columns, text=label, padding=6)
            frame.grid(row=0, column=i, sticky='nsew', padx=3)
            canvas = tk.Canvas(frame, height=118, background='#194b3d', highlightthickness=0)
            canvas.pack(fill='x')
            canvas.bind('<Configure>', lambda e: self.render())
            text = ScrolledText(frame, wrap='word', font=('Microsoft YaHei', 10), width=20)
            text.pack(fill='both', expand=True, pady=(6, 0))
            self.canvases.append(canvas)
            self.texts.append(text)
        self.records, self.decisions, self.context = [], [], None
        self.result = None

    @staticmethod
    def put(widget, text):
        if widget.get('1.0', 'end-1c') != text:
            widget.configure(state='normal')
            widget.delete('1.0', 'end')
            widget.insert('1.0', text)
            widget.configure(state='disabled')

    def update_records(self, records, decisions, context):
        selected = self.record_box.get() if context == self.context else ''
        self.context, self.records, self.decisions = context, records, decisions
        labels = [f"动作 {r['action_index']} · 玩家 {r['player']} · 切 {Tile(**r['comparison']['selected']['discard']['tile'])}" for r in records]
        self.record_box['values'] = labels
        self.record_box.set(selected if selected in labels else labels[-1] if labels else '')
        self.select_record(preserve=bool(selected and selected in labels))

    def select_record(self, preserve=False):
        old = self.candidate_box.get() if preserve else ''
        self.choices = []
        if self.records and self.record_box.current() >= 0:
            record = self.records[self.record_box.current()]
            self.choices = [c['discard'] for c in record['analysis']['candidates']]
        labels = [f"{Tile(**d['tile'])} · {'摸切' if d['is_tsumogiri'] else '手切'}" for d in self.choices]
        self.candidate_box['values'] = labels
        default = 0
        if self.choices:
            actual = record['comparison']['selected']['discard']
            default = next((i for i, c in enumerate(record['analysis']['candidates'])
                            if c['discard'] != actual and c['shanten'] == 0), 0)
        self.candidate_box.set(old if old in labels else labels[default] if labels else '')
        self.render()

    def focus_record(self, action_index):
        for i, record in enumerate(self.records):
            if record['action_index'] == action_index:
                self.record_box.current(i)
                self.select_record()
                return

    def render(self):
        if not hasattr(self, 'records'):
            return
        if not self.records or self.candidate_box.current() < 0:
            self.result = None
            self.put(self.summary, '当前视角及回放位置尚无弃牌分析记录。')
            for canvas, text in zip(self.canvases, self.texts):
                canvas.delete('all')
                self.put(text, '')
            return
        record = self.records[self.record_box.current()]
        decision = next((r for r in self.decisions if r['action_index'] == record['action_index']), None)
        self.result = compare_candidates(record, self.choices[self.candidate_box.current()], decision)
        self.put(self.summary, self.result['version'] + ' · 只比较，不重新推荐\n' + '\n'.join(self.result['messages']))
        for side, canvas, text in zip(('actual', 'alternative'), self.canvases, self.texts):
            view = self.result[side]
            self.draw_projection(canvas, view)
            self.put(text, self.describe(view, record))

    @staticmethod
    def draw_projection(canvas, view):
        canvas.delete('all')
        width = max(canvas.winfo_width(), 300)
        canvas.create_text(8, 10, anchor='w', text='候选分析 · 切后暗手', fill='white', font=('Microsoft YaHei', 9))
        cut = Tile(**view['discard']['tile'])
        canvas.create_text(width - 8, 10, anchor='e', text=f"切 {cut} · {'摸切' if view['discard']['is_tsumogiri'] else '手切'}",
                           fill='#ffe3a5', font=('Microsoft YaHei', 9))
        def cards(tiles, y):
            step = min(31, (width - 16) / max(len(tiles), 1))
            for i, t in enumerate(tiles):
                tile = Tile(**t) if isinstance(t, dict) else Tile.parse(t)
                x = 8 + i * step
                canvas.create_rectangle(x, y, x + step - 2, y + 32, fill='#fbf7e9', outline='#c9c0a5')
                label = ('東南西北白發中'[tile.rank - 1] if tile.suit == 'z' else
                         f"{'赤' if tile.is_red else tile.rank}{dict(m='萬', p='筒', s='索')[tile.suit]}")
                canvas.create_text(x + (step - 2) / 2, y + 16, text=label, font=('Microsoft YaHei', 9),
                                   fill='#b42c32' if tile.is_red else '#234b3e')
        cards(view['hand'], 22)
        tenpai = view['tenpai']
        canvas.create_text(8, 65, anchor='w', text='理论等待 · 状态见下方文字' if tenpai and tenpai.get('waits') else '等待：不适用 / 无可用分析',
                           fill='white', font=('Microsoft YaHei', 9))
        cards([w['tile'] for w in tenpai.get('waits', [])] if tenpai else [], 77)

    def describe(self, view, record):
        basic, tenpai = view['basic'], view['tenpai']
        lines = [f"切 {Tile(**view['discard']['tile'])} · {basic['shanten']} 向听 / {basic['ukeire']} 枚进张",
                 '基础牌效最优／并列最优' if basic['is_best'] else '非基础牌效最优；不代表综合决策错误']
        if view['melds']:
            lines.append('副露：' + '；'.join(m['kind'] + ' ' + ' '.join(str(Tile(**t)) for t in m['tiles']) for m in view['melds']))
        if view['mode']:
            lines.append('历史评估模式：' + ('弃和（允许牌效损失）' if view['mode'] == 'fold' else '进攻'))
        if basic['shanten'] != 0:
            lines.append('未听牌：不适用听牌比较。')
        elif tenpai is None:
            lines.append('历史听牌分析缺失或版本不支持，分析受限。')
        else:
            lines.extend([f"理论 {tenpai['shape_wait_types']} 种 / 未见 {tenpai['shape_unseen']} 枚；尚有未见 {tenpai['available_wait_types']} 种",
                          '荣和限制：' + ('、'.join(LABELS[r] for r in tenpai['ron_restrictions']) or '当前无已知振听限制'),
                          '仅解除临时振听后：' + LABELS[tenpai['ron_state_after_own_draw']] + '（等待不变、无新见逃）'])
            for w in tenpai['waits']:
                for v in w['variants']:
                    lines.append(f"\n{v['tile']} · 未见 {v['unseen']} 枚" + ('【已见尽】' if v['unseen'] == 0 else ''))
                    for kind, name in (('ron', '荣和'), ('tsumo', '自摸')):
                        item = v[kind]
                        lines.append(f"  {name}：{'有役' if item['has_yaku'] else '无役'} / {LABELS[item['permission']]}")
                        if self.details.get() and item['value']:
                            value, dealer = item['value'], record['player'] == record['observation']['dealer_seat']
                            cost = value['cost']
                            payment = (f"放铳者 {cost['main']}" if kind == 'ron' else f"每家 {cost['main']}" if dealer
                                       else f"庄家 {cost['main']} / 另两家各 {cost['additional']}")
                            lines.extend([f"  {value['han']} 番 {value['fu']} 符 · 基础支付 {payment}",
                                          '  ' + '、'.join(y['name'] for y in value['yaku'])])
                        if item['score_status'] != 'known':
                            lines.append('  双立直状态未知：点数仅按普通已成立立直条件。')
        if view['defense']:
            lines.append('防守依据（独立，不是放铳概率）：')
            lines.extend(f"对玩家 {e['player']}：{e['reason']}" for e in view['defense']['opponents'])
        else:
            lines.append('防守依据：本记录未保存／不可用，不等于安全。')
        lines.append('\n未见不是活牌山余量；条件支付不含本场、供托、责任支付，不是期望收益。')
        if self.details.get():
            lines.extend(self.result['assumptions'].values())
        return '\n'.join(lines)
