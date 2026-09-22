"""Lazy, read-only developer analysis browser. Workers never access the game."""

from copy import deepcopy
import json
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from .comparison_ui import LABELS
from .models import Discard, Tile
from .progression import analyze_progression
from .riichi_decision import analyze_riichi


def waiting_text(candidate, details=False):
    if candidate.get("status") != "applicable":
        return "不适用听牌分析"
    lines = [f"理论 {candidate['shape_wait_types']} 种 / 未见 {candidate['shape_unseen']} 枚",
             "荣和限制：" + ("、".join(LABELS[r] for r in candidate['ron_restrictions']) or "无已知振听限制"),
             "仅解除临时振听后：" + LABELS[candidate['ron_state_after_own_draw']]]
    for wait in candidate['waits']:
        for variant in wait['variants']:
            lines.append(f"{variant['tile']} · 未见 {variant['unseen']} 枚" + ("【已见尽】" if not variant['unseen'] else ""))
            for kind, label in (("ron", "荣和"), ("tsumo", "自摸")):
                item, value = variant[kind], variant[kind]['value']
                line = f"  {label}：{LABELS[item['permission']]}"
                if value:
                    line += ' · 有役'
                if value and details:
                    cost = value['cost']
                    payment = (f"放铳者 {cost['main']}" if kind == 'ron' else
                               f"每家 {cost['main']}" if cost['main'] == cost['additional'] else
                               f"庄家 {cost['main']} / 另两家各 {cost['additional']}")
                    line += f" · {value['han']} 番 {value['fu']} 符 · 基础支付 {payment}"
                    line += " · " + "、".join(y['name'] for y in value['yaku'])
                lines.append(line)
                if item['score_status'] != 'known':
                    lines.append('  双立直资格未知：点数仅按普通已成立立直条件。')
    lines.append("条件基础支付不含本场、供托或责任支付；不是期望收益。")
    return '\n'.join(lines)


class PreviewPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=8)
        self.records, self.decisions, self.context = [], [], None
        self.token, self.cache, self.queue = 0, {}, Queue()
        self.live = None
        self.selected_record = None
        self.details = tk.BooleanVar(value=False)
        row = ttk.Frame(self)
        row.pack(fill='x')
        self.record_box = ttk.Combobox(row, state='readonly', width=26)
        self.record_box.pack(side='left')
        self.record_box.bind('<<ComboboxSelected>>', lambda e: self.select_record())
        self.cut_box = ttk.Combobox(row, state='readonly', width=16)
        self.cut_box.pack(side='left', padx=6)
        self.cut_box.bind('<<ComboboxSelected>>', lambda e: self.clear_paths())
        ttk.Button(row, text='分析一向听推进', command=self.compute).pack(side='left')
        ttk.Button(row, text='查看立直／默听', command=self.show_riichi).pack(side='left', padx=6)
        row = ttk.Frame(self)
        row.pack(fill='x', pady=8)
        ttk.Checkbutton(row, text='展开番符支付', variable=self.details,
                        command=self.redraw_details).pack(side='right')
        ttk.Label(row, text='假设摸牌 →').pack(side='left')
        self.draw_box = ttk.Combobox(row, state='readonly', width=18)
        self.draw_box.pack(side='left', padx=6)
        self.draw_box.bind('<<ComboboxSelected>>', lambda e: self.select_draw())
        ttk.Label(row, text='下一次弃牌 → 听牌').pack(side='left')
        self.branch_box = ttk.Combobox(row, state='readonly', width=18)
        self.branch_box.pack(side='left', padx=6)
        self.branch_box.bind('<<ComboboxSelected>>', lambda e: self.show_branch())
        self.text = ScrolledText(self, wrap='word', font=('Microsoft YaHei', 10))
        self.text.pack(fill='both', expand=True)
        self.put('选择历史切牌查看条件投影；实际立直选择请使用牌桌操作区。')

    def put(self, text):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', text)
        self.text.configure(state='disabled')

    def update_records(self, records, decisions, context):
        changed = context != self.context
        self.records, self.decisions, self.context = records, decisions, context
        old = self.record_box.get()
        labels = [f"动作 {r['action_index']} · 玩家 {r['player']}" for r in records]
        self.record_box['values'] = labels
        if changed:
            self.live = None
            self.cache.clear()
        current = next((r for r, label in zip(records, labels) if label == old), None)
        if changed or old not in labels or current != self.selected_record:
            self.record_box.set(labels[-1] if labels else '')
            self.select_record()

    def select_record(self):
        self.live = None
        self.clear_paths()
        self.choices = []
        if self.records and self.record_box.current() >= 0:
            self.selected_record = self.records[self.record_box.current()]
            self.choices = [c['discard'] for c in self.selected_record['analysis']['candidates']]
        else:
            self.selected_record = None
        self.cut_box['values'] = [self.label(d) for d in self.choices]
        self.cut_box.set(self.label(self.choices[0]) if self.choices else '')

    @staticmethod
    def label(discard):
        return f"{Tile(**discard['tile'])} · {'摸切' if discard['is_tsumogiri'] else '手切'}"

    def focus_record(self, action_index, cut):
        for i, record in enumerate(self.records):
            if record['action_index'] == action_index:
                self.record_box.current(i)
                self.select_record()
                if cut in self.choices:
                    self.cut_box.current(self.choices.index(cut))
                return

    def clear_paths(self):
        self.token += 1
        self.live = None
        self.display_kind = None
        self.draw_box.set('')
        self.branch_box.set('')
        self.draw_box['values'] = self.branch_box['values'] = ()
        self.put('候选投影；请选择分析。不会改变真实牌局或机器人选择。')

    def compute(self):
        if not self.records or self.record_box.current() < 0 or self.cut_box.current() < 0:
            self.put('当前无可用历史记录。')
            return
        record = self.records[self.record_box.current()]
        cut = self.choices[self.cut_box.current()]
        self.clear_paths()
        token = self.token
        key = json.dumps([record['observation'], cut], sort_keys=True)
        if key in self.cache:
            self.show_progression(self.cache[key])
            return
        observation = deepcopy(record['observation'])
        action = Discard(**{**cut, 'tile': Tile(**cut['tile'])})
        self.put('正在计算所选弃牌的全部有效摸牌分支… 可继续对局；结果仅属于此历史观察。')
        def worker():
            try:
                result = analyze_progression(observation, [action])
            except (ValueError, TypeError, KeyError) as exc:
                result = str(exc)
            self.queue.put((token, key, result))
        Thread(target=worker, daemon=True).start()
        self.after(100, self.collect)

    def collect(self):
        try:
            token, key, result = self.queue.get_nowait()
        except Empty:
            if self.winfo_exists():
                self.after(100, self.collect)
            return
        if token == self.token:
            if isinstance(result, str):
                self.put('分析受限：' + result)
            else:
                if len(self.cache) >= 16:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[key] = result
                self.show_progression(result)

    def show_progression(self, result):
        self.progression = result
        candidate = result['candidates'][0]
        self.draws = candidate['draws']
        if not self.draws:
            self.put(candidate.get('reason', '非一向听或没有尚未见的有效摸牌；不适用推进比较。'))
            return
        self.draw_box['values'] = [f"{d['tile']} · 未见 {d['unseen']}" for d in self.draws]
        self.draw_box.current(0)
        self.select_draw()

    def select_draw(self):
        if self.draw_box.current() < 0:
            return
        self.branches = self.draws[self.draw_box.current()]['branches']
        self.branch_box['values'] = [self.label(b['discard']) for b in self.branches]
        if self.branches:
            self.branch_box.current(0)
            self.show_branch()
        else:
            self.put('没有允许的听牌弃牌分支，分析受限。')

    def show_branch(self):
        if self.branch_box.current() < 0:
            return
        draw = self.draws[self.draw_box.current()]
        branch = self.branches[self.branch_box.current()]
        self.display_kind = 'progression'
        self.put(f"候选投影：切 {self.cut_box.get()} → 摸 {draw['tile']} → 切 {self.label(branch['discard'])}\n\n" +
                 waiting_text(branch, self.details.get()) + '\n\n' + '\n'.join(self.progression['assumptions']) + '\n' +
                 '\n'.join(draw['assumptions'].values()))

    def show_live(self, observation, legal, mode):
        self.clear_paths()
        self.live = analyze_riichi(observation, legal, mode)
        self.show_riichi()

    def show_riichi(self):
        self.token += 1
        self.display_kind = 'riichi'
        result = self.live
        if result is None and self.records and self.record_box.current() >= 0:
            record = self.records[self.record_box.current()]
            decision = next((d for d in self.decisions if d['action_index'] == record['action_index']), {})
            result = decision.get('riichi')
        if result is None:
            self.put('旧记录未记录立直评价，不能从之后的信息补猜。')
            return
        lines = ['行动前条件比较' if self.live else '历史行动前立直比较', *result['assumptions']]
        for row in result['candidates']:
            lines.extend(['\n切 ' + self.label(row['discard']), row['reason'],
                          '开发基准建议：' + ('立直' if row['recommend_riichi'] else '默听'),
                          '\n默听：', waiting_text(row['dama'], self.details.get()), '\n假设立直成立（另支出1000点）：', waiting_text(row['riichi'], self.details.get())])
        if not result['candidates']:
            lines.append('行动前没有核心提供的合法立直动作。')
        self.put('\n'.join(lines))

    def redraw_details(self):
        if self.display_kind == 'riichi':
            self.show_riichi()
        elif self.display_kind == 'progression':
            self.show_branch()
