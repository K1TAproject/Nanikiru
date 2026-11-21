"""Single-window manual simulation UI; strategy inputs always use observed records."""

from dataclasses import fields, replace
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from nanikiru.analysis import analyze
from nanikiru.analysis.output import debug_json, summarize_result
from nanikiru.models import UNKNOWN
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.processing.records import replay
from nanikiru.rules.config import RuleConfig, tenhou_hanchan
from nanikiru.simulation import Simulator
from nanikiru.simulation.persistence import save_simulation, load_simulation, replay_simulation
from nanikiru.tiles import index
from test_ui.tiles import draw_face

LABELS = dict(open_tanyao="食断", kan_dora="杠宝", ura_dora="里宝", kan_ura="杠里宝", aka_dora="赤宝",
              forbid_kuikae="禁止食替", multiple_ron="多家荣和", ippatsu="一发", bankruptcy="负分击飞",
              kiriage="切上满贯", kazoe_yakuman="累计役满", double_yakuman="特殊双倍役满",
              nagashi_mangan="流局满贯", abortive_draws="途中流局", pao="包牌")
NAMES = dict(discard="弃牌", riichi="立直", chi="吃", pon="碰", open_kan="大明杠", closed_kan="暗杠",
             added_kan="加杠", tsumo="自摸", ron="荣和", **{"pass":"过"}, abort_nine="九种九牌",
             draw="摸牌", riichi_accepted="立直成立", kan_declared="杠宣言", response="响应", ended="结算")
PHASES = dict(await_draw="待摸牌",await_discard="待弃牌",await_response="弃牌响应",await_kan_response="抢杠响应",
              await_replacement="待岭上补牌",ended="已结束")


def result_text(result):
    if result is None:
        return '尚未结算。'
    names=dict(win='和牌',exhaustive='荒牌流局',nine_terminals='九种九牌',four_winds='四风连打',four_riichi='四家立直',four_kans='四杠散了',three_ron='三家荣和流局')
    lines=[names.get(result['kind'],result['kind']), '结算后点数：'+ ' / '.join(map(str,result['scores']))]
    for hand in result['details'].get('hands',[]):
        score=hand['score']
        lines.append(f"{hand['seat']} 家：{score['han']} 番 {score['fu']} 符 · "+'、'.join(y['name'] for y in score['yaku']))
    for payment in result['payments']:
        lines.append(f"{payment['payer']} 家 → {payment['winner']} 家：{payment['amount']} 点 ({payment['reason']})")
    if result['details'].get('deposits'):
        lines.append(f"供托 {result['details']['deposits']} 点 → {result['details']['deposit_winner']} 家")
    if result['details'].get('nagashi'):
        lines.append('流局满贯：'+str(result['details']['nagashi']))
    lines.append('庄家连庄' if result['dealer_repeats'] else '换庄')
    if result['bankruptcy']: lines.append('已触发负分击飞')
    n=result['next_round']
    if result.get('match',{}).get('ended'):
        lines.append('半庄结束；顺位：'+' / '.join(map(str,result['match']['ranking'])))
    else:
        lines.append(f"下一局：{n['round_wind']} {n['hand_number']} 局，庄家 {n['dealer']}，{n['honba']} 本场；不自动开局。")
    return '\n'.join(lines)+'\n\n完整结算数据：\n'+debug_json(result)


class SimulatorApp:
    def __init__(self, root):
        self.root = root
        root.title("Nanikiru · 连续单局模拟器")
        root.geometry("1450x950"); root.minsize(1200, 820)
        self.game, self.cursor, self.last_request, self.last_result = None, None, None, None
        self.running = False
        self.timer = None
        self.observer, self.operator = tk.IntVar(value=0), tk.IntVar(value=0)
        self.omniscient = tk.BooleanVar(value=False)
        self.seed, self.wind, self.dealer = tk.StringVar(value=""), tk.StringVar(value="east"), tk.IntVar(value=0)
        self.honba, self.sticks, self.hand_number = tk.StringVar(value="0"), tk.StringVar(value="0"), tk.StringVar(value="1")
        self.points, self.match_length = tk.StringVar(value="25000"), tk.StringVar(value="east")
        self.score_text = tk.StringVar(value="")
        self.rule_base = RuleConfig()
        self.rule_profile = tk.StringVar(value='原自定义规则')
        self.rule_vars = {f.name: tk.BooleanVar(value=getattr(RuleConfig(),f.name)) for f in fields(RuleConfig()) if f.name in LABELS}
        self.show_rules, self.show_json = tk.BooleanVar(), tk.BooleanVar()
        self.message, self.status, self.summary = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.path = tk.StringVar(value=str(Path.cwd()/"simulation.json"))
        self.overwrite = tk.BooleanVar()
        self.method = tk.StringVar(value="minimal-shape-v1")
        self.method_options = tk.StringVar(value="{}")
        self._build()
        self.new_game()

    def _build(self):
        top=ttk.Frame(self.root,padding=8); top.pack(fill='x')
        for label,var,width,values in [('种子',self.seed,8,None),('场风',self.wind,6,('east','south','west','north')),
                                      ('局数',self.hand_number,3,None),('庄家',self.dealer,3,(0,1,2,3)),
                                      ('本场',self.honba,3,None),('供托',self.sticks,3,None)]:
            ttk.Label(top,text=label).pack(side='left',padx=3)
            widget=ttk.Combobox(top,textvariable=var,values=values,width=width,state='readonly') if values else ttk.Entry(top,textvariable=var,width=width)
            widget.pack(side='left')
        ttk.Button(top,text='新建单局',command=self.new_game).pack(side='left',padx=6)
        ttk.Checkbutton(top,text='规则设置（新局生效）',variable=self.show_rules,command=self.toggle_rules).pack(side='left')
        profile=ttk.Combobox(top,textvariable=self.rule_profile,values=('原自定义规则','天凤四人半庄'),state='readonly',width=16)
        profile.pack(side='left',padx=5)
        profile.bind('<<ComboboxSelected>>',lambda e:self.select_rule_profile())
        self.rules_frame=ttk.LabelFrame(self.root,text='默认规则与场况',padding=5)
        for i,(key,var) in enumerate(self.rule_vars.items()):
            ttk.Checkbutton(self.rules_frame,text=LABELS[key],variable=var).grid(row=i//8,column=i%8,sticky='w',padx=8)
        ttk.Label(self.rules_frame,text='起点').grid(row=2,column=0)
        ttk.Entry(self.rules_frame,textvariable=self.points,width=8).grid(row=2,column=1)
        ttk.Label(self.rules_frame,text='场制').grid(row=2,column=2)
        ttk.Combobox(self.rules_frame,textvariable=self.match_length,values=('east','south'),state='readonly',width=8).grid(row=2,column=3)
        ttk.Label(self.rules_frame,text='四家点数（留空用起点）').grid(row=2,column=4,columnspan=2)
        ttk.Entry(self.rules_frame,textvariable=self.score_text,width=28).grid(row=2,column=6,columnspan=2)
        self.toolbar=ttk.Frame(self.root,padding=5); self.toolbar.pack(fill='x')
        ttk.Entry(self.toolbar,textvariable=self.path,width=38).pack(side='left')
        ttk.Checkbutton(self.toolbar,text='允许覆盖',variable=self.overwrite).pack(side='left')
        ttk.Button(self.toolbar,text='保存全知存档',command=self.save).pack(side='left')
        ttk.Button(self.toolbar,text='加载',command=self.load).pack(side='left')
        ttk.Button(self.toolbar,text='撤销动作',command=self.undo).pack(side='left',padx=4)
        ttk.Label(self.toolbar,text='分析视角').pack(side='left',padx=4)
        combo=ttk.Combobox(self.toolbar,textvariable=self.observer,values=(0,1,2,3),width=3,state='readonly'); combo.pack(side='left')
        combo.bind('<<ComboboxSelected>>',lambda e:self.change_view())
        ttk.Checkbutton(self.toolbar,text='全知调试显示（不进入分析）',variable=self.omniscient,command=self.refresh).pack(side='left',padx=5)
        ttk.Label(self.root,textvariable=self.status,padding=5).pack(fill='x')
        self.footer=ttk.Label(self.root,textvariable=self.message,padding=5,foreground='#9b4818'); self.footer.pack(side='bottom',fill='x')
        self.body=ttk.Panedwindow(self.root,orient='horizontal'); self.body.pack(fill='both',expand=True,padx=8,pady=4)
        left=ttk.Frame(self.body); right=ttk.Frame(self.body); self.body.add(left,weight=3); self.body.add(right,weight=2)
        self.boards=[]
        table=ttk.Frame(left); table.pack(fill='both',expand=True)
        for s in range(4):
            box=ttk.LabelFrame(table,text=f'{s} 家'); box.grid(row=s//2,column=s%2,sticky='nsew',padx=2,pady=2)
            canvas=tk.Canvas(box,width=390,height=200,bg='#edf3ef',highlightthickness=0)
            scroll=ttk.Scrollbar(box,command=canvas.yview); scroll.pack(side='right',fill='y')
            canvas.configure(yscrollcommand=scroll.set); canvas.pack(fill='both',expand=True)
            self.boards.append(canvas)
        for i in range(2): table.rowconfigure(i,weight=1); table.columnconfigure(i,weight=1)
        self.hand_canvas=tk.Canvas(left,height=85,bg='#e3ede8',highlightthickness=0); self.hand_canvas.pack(fill='x',pady=4)
        controls=ttk.Frame(left); controls.pack(fill='x')
        ttk.Label(controls,text='操作玩家').pack(side='left')
        combo=ttk.Combobox(controls,textvariable=self.operator,values=(0,1,2,3),width=3,state='readonly'); combo.pack(side='left')
        combo.bind('<<ComboboxSelected>>',lambda e:self.refresh())
        self.action_combo=ttk.Combobox(controls,state='readonly',width=35); self.action_combo.pack(side='left',fill='x',expand=True,padx=5)
        self.execute_button=ttk.Button(controls,text='执行所选动作',command=self.execute); self.execute_button.pack(side='left')
        row=ttk.Frame(left); row.pack(fill='x',pady=5)
        ttk.Button(row,text='定位下一操作者',command=self.next_operator).pack(side='left')
        ttk.Button(row,text='测试推进一步',command=self.step).pack(side='left',padx=4)
        self.auto_button=ttk.Button(row,text='自动测试（摸切/过/和牌）',command=self.toggle_auto); self.auto_button.pack(side='left')
        ttk.Label(left,text='测试驱动只提交合法动作，不是何切策略；新建会替换未保存的当前局。',wraplength=650).pack(fill='x')
        self.indicators=tk.Canvas(right,height=65,bg='#f5f3ed',highlightthickness=0); self.indicators.pack(fill='x')
        row=ttk.Frame(right); row.pack(fill='x')
        ttk.Label(row,text='可见事件（点击回放）').pack(side='left')
        ttk.Button(row,text='回到最新',command=self.latest).pack(side='right')
        history_box=ttk.Frame(right); history_box.pack(fill='both',expand=True)
        self.history=ttk.Treeview(history_box,columns=('fact',),show='tree headings',height=5)
        self.history.heading('#0',text='步'); self.history.column('#0',width=40,stretch=False)
        self.history.heading('fact',text='已成立事实 / 本人响应'); self.history.column('fact',width=350)
        scroll=ttk.Scrollbar(history_box,command=self.history.yview); scroll.pack(side='right',fill='y')
        self.history.configure(yscrollcommand=scroll.set); self.history.pack(fill='both',expand=True)
        self.history.bind('<<TreeviewSelect>>',self.select_history)
        method_row=ttk.Frame(right); method_row.pack(fill='x',pady=4)
        combo=ttk.Combobox(method_row,textvariable=self.method,values=('basic-v1','minimal-shape-v1','fixed-v2'),state='readonly',width=22); combo.pack(side='left')
        combo.bind('<<ComboboxSelected>>',lambda e:self.refresh())
        ttk.Checkbutton(method_row,text='实际输入/输出 JSON',variable=self.show_json,command=self.toggle_json).pack(side='left')
        params=ttk.Frame(right); params.pack(fill='x')
        ttk.Label(params,text='v2 权重 JSON').pack(side='left')
        ttk.Entry(params,textvariable=self.method_options,width=25).pack(side='left',fill='x',expand=True)
        ttk.Button(params,text='应用',command=self.refresh).pack(side='left')
        summary_label=ttk.Label(right,textvariable=self.summary,wraplength=470,justify='left')
        summary_label.pack(fill='x',pady=4)
        summary_label.bind('<Configure>',lambda e:summary_label.configure(wraplength=max(100,e.width-8)))
        self.json_frame=ttk.Frame(right)
        self.input_text=self._text_box(self.json_frame,'实际输入',6)
        self.output_text=self._text_box(self.json_frame,'实际输出',6)
        self.result_text=self._text_box(right,'结算：支付 / 役种 / 下一局场况',6)

    def _text_box(self,parent,title,height):
        frame=ttk.LabelFrame(parent,text=title); frame.pack(fill='both',expand=True)
        text=tk.Text(frame,height=height,width=48,wrap='none',font=('Consolas',9))
        y=ttk.Scrollbar(frame,command=text.yview); y.pack(side='right',fill='y')
        x=ttk.Scrollbar(frame,orient='horizontal',command=text.xview); x.pack(side='bottom',fill='x')
        text.configure(yscrollcommand=y.set,xscrollcommand=x.set); text.pack(fill='both',expand=True)
        return text

    @staticmethod
    def _write(widget,text):
        widget.configure(state='normal'); widget.delete('1.0','end'); widget.insert('1.0',text); widget.configure(state='disabled')

    def toggle_rules(self):
        if self.show_rules.get(): self.rules_frame.pack(fill='x',before=self.toolbar)
        else: self.rules_frame.pack_forget()

    def select_rule_profile(self):
        self.rule_base=tenhou_hanchan() if self.rule_profile.get()=='天凤四人半庄' else RuleConfig()
        for key,var in self.rule_vars.items():var.set(getattr(self.rule_base,key))
        self.match_length.set(self.rule_base.match_length)
        self.points.set(str(self.rule_base.starting_points))
        self.message.set('规则预设已选择，点击新建单局后生效；修改开关后属于自定义变体。')

    def toggle_json(self):
        if self.show_json.get(): self.json_frame.pack(fill='both',expand=True)
        else: self.json_frame.pack_forget()

    def _stop(self):
        self.running=False; self.auto_button.configure(text='自动测试（摸切/过/和牌）')
        if self.timer is not None:
            self.root.after_cancel(self.timer); self.timer=None

    def new_game(self):
        try:
            rules=replace(self.rule_base,**{k:v.get() for k,v in self.rule_vars.items()},starting_points=int(self.points.get()),match_length=self.match_length.get())
            scores=[int(x) for x in self.score_text.get().split()] if self.score_text.get().strip() else None
            game=Simulator(rules=rules,seed=self.seed.get() or None,dealer=self.dealer.get(),round_wind=self.wind.get(),
                           hand_number=int(self.hand_number.get()),honba=int(self.honba.get()),sticks=int(self.sticks.get()),scores=scores)
            self._stop(); self.game=game; self.cursor=None; self.operator.set(game.actor); self.refresh()
            self.message.set('已新建单局；规则已固定。合法响应为空时自动继续。')
        except (ValueError,tk.TclError) as error: self.message.set(str(error))

    def refresh(self):
        if self.game is None: return
        self.last_request=self.last_result=None
        seat=self.observer.get(); record=self.game.record(seat)
        count=len(record.events) if self.cursor is None else self.cursor
        position=replay(record,count)
        self.status.set(f'分析视角 {seat} · 操作玩家 {self.operator.get()} · {position.round_wind} {position.hand_number} 局 / {position.honba} 本场 · '
                        f'余牌 {position.remaining_draws} · {PHASES.get(position.phase,position.phase)} · 可见事件 {count}/{len(record.events)}'
                        + (' · 只读回放' if self.cursor is not None else '') + (' · 全知调试开启' if self.omniscient.get() else ''))
        self._draw_table(position,record.events[:count])
        self.history.unbind('<<TreeviewSelect>>')
        self.history.delete(*self.history.get_children()); self.history.insert('','end',iid='0',text='0',values=('配牌快照',))
        for e in record.events:
            fact=e.data.get('fact',{}); kind=e.kind.removeprefix('sim_')
            description=f'{e.actor if e.actor is not None else "系统"} · {NAMES.get(kind,kind)}'
            if fact.get('code') is not None: description+=' · '+('未知' if fact['code'] is UNKNOWN else fact['code'])
            self.history.insert('','end',iid=str(e.id),text=e.id,values=(description,))
        self.history.selection_set(str(count)); self.history.see(str(count))
        self.root.after_idle(lambda:self.history.bind('<<TreeviewSelect>>',self.select_history))
        tasks=('shanten','tenpai','discard_shape')+(('recommend',) if self.method.get()!='basic-v1' else ())
        try:
            options=json.loads(self.method_options.get()) if self.method.get()=='fixed-v2' else {}
            request=build_analysis_request(record,source_version=self.game.version,through=count,tasks=tasks,method=self.method.get(),method_options=options)
            result=analyze(request); self.last_request,self.last_result=request,result
            self.summary.set(summarize_result(request,result))
            self._write(self.input_text,debug_json(request)); self._write(self.output_text,debug_json(result))
        except ValueError as error:
            self.summary.set(f'当前分析不可用：{error}'); self._write(self.input_text,''); self._write(self.output_text,str(error))
        ended=next((e.data['fact']['result'] for e in reversed(record.events[:count]) if e.kind=='sim_ended'),None)
        self._write(self.result_text,result_text(ended))
        self._actions()

    def _draw_table(self,position,events):
        called={t.id for t in position.tiles if t.zone=='meld'}
        result=next((e.data['fact']['result'] for e in reversed(events) if e.kind=='sim_ended'),None)
        revealed={h['seat']:h for h in result['details'].get('hands',[])} if result else {}
        debug=self.game.debug_state() if self.omniscient.get() and self.cursor is None else None
        for s,canvas in enumerate(self.boards):
            canvas.delete('all')
            ready=any(e.kind=='sim_riichi_accepted' and e.actor==s for e in events)
            score=next(p.score for p in position.players if p.seat==s)
            canvas.create_text(8,12,anchor='w',text=f'{s} 家 · {score} 点'+(' · 庄' if position.dealer_seat==s else '')+(' · 立直' if ready else ''),font=('Microsoft YaHei',10,'bold'))
            hand=[t.code for t in position.tiles if t.zone=='hand' and t.seat==s]
            if debug: hand=[debug['codes'][t] for t in debug['hands'][s]]
            elif s!=position.observer_seat: hand=[UNKNOWN]*next(p.untracked_hand_count for p in position.players if p.seat==s)
            if s in revealed:
                hand=revealed[s]['concealed'][:]
                if result['details']['source'] is not None: hand.append(revealed[s]['win_tile'])
            hand=sorted(hand,key=lambda c: 99 if c is UNKNOWN else index(c))
            for i,c in enumerate(hand):
                if c is UNKNOWN: canvas.create_rectangle(8+i*22,28,27+i*22,58,fill='#47746b',outline='#294e47')
                else: draw_face(canvas,c,8+i*22,28,18,28)
            discards=[e.data['fact'] for e in events if e.kind=='sim_discard' and e.actor==s]
            for i,fact in enumerate(discards):
                x,y=8+(i%10)*30,70+(i//10)*43
                draw_face(canvas,fact['code'],x,y,25,35)
                if fact['tile'] in called: canvas.create_line(x,y,x+25,y+35,fill='#b73536',width=2)
                if fact.get('riichi'): canvas.create_rectangle(x-1,y-1,x+27,y+38,outline='#b73536',width=2)
            y=78+max(1,(len(discards)+9)//10)*43
            x=8
            for gid,g in position.groups.items():
                members=[t for t in position.tiles if t.group_id==gid and t.seat==s]
                if not members: continue
                canvas.create_text(x,y,anchor='nw',text=f'{NAMES.get(g["kind"],g["kind"])} ← {g["source_seat"] if g["source_seat"] is not None else "自"}',font=('Microsoft YaHei',8))
                for j,t in enumerate(members): draw_face(canvas,t.code,x+j*20,y+18,17,25)
                x+=len(members)*20+7
            canvas.configure(scrollregion=(0,0,370,y+54))
        self.indicators.delete('all'); self.indicators.create_text(5,10,anchor='w',text='宝牌指示牌')
        for i,t in enumerate(t for t in position.tiles if t.zone=='dora'): draw_face(self.indicators,t.code,90+i*35,5,29,43)

    def _actions(self):
        self.hand_canvas.delete('all'); op=self.operator.get()
        self.hand_hitboxes=[]
        self.hand_canvas.bind('<Button-1>',self.click_hand)
        position=self.game.observe(op) if self.cursor is None else None
        self.actions=list(self.game.legal_actions(op)) if position else []
        by_id={t.id:t.code for t in position.tiles} if position else {}
        labels=[NAMES[a.kind]+('  '+' '.join(by_id.get(t,t) for t in a.tile_ids) if a.tile_ids else '') for a in self.actions]
        self.action_combo.configure(values=labels)
        if labels: self.action_combo.current(0)
        else: self.action_combo.set('回放中' if self.cursor is not None else '该玩家当前无待选动作')
        self.execute_button.configure(state='normal' if labels else 'disabled')
        self.hand_canvas.create_text(5,10,anchor='w',text=f'操作玩家 {op} 的手牌（分析仍为 {self.observer.get()} 家视角）')
        if position:
            hand=sorted([t for t in position.tiles if t.zone=='hand'],key=lambda t:(index(t.code),t.code))
            drawn=position.draw_context.get('tile_id') if isinstance(position.draw_context,dict) else None
            hand.sort(key=lambda t:t.id==drawn)
            for i,t in enumerate(hand):
                x=6+i*39+(10 if t.id==drawn else 0); draw_face(self.hand_canvas,t.code,x,25,31,45)
                self.hand_canvas.create_rectangle(x,25,x+33,73,fill='',outline='#b58632' if t.id==drawn else '')
                self.hand_hitboxes.append((x,x+33,t.id))

    def click_hand(self,event):
        if 25<=event.y<=73:
            for left,right,tile_id in self.hand_hitboxes:
                if left<=event.x<=right: self.select_tile(tile_id); break

    def select_tile(self,tile_id):
        for i,a in enumerate(self.actions):
            if a.kind=='discard' and a.tile_ids==(tile_id,): self.action_combo.current(i); break
        if self.last_request and self.observer.get()==self.operator.get():
            self.summary.set(summarize_result(self.last_request,self.last_result,tile_id))

    def execute(self):
        if self.cursor is not None or not self.actions: return
        try:
            self.game.submit(self.actions[self.action_combo.current()]); self.next_operator()
        except ValueError as error: self.message.set(str(error))

    def next_operator(self):
        if self.cursor is None:
            seats=[s for s in range(4) if self.game.legal_actions(s)]
            if seats: self.operator.set(seats[0])
        self.refresh()

    def step(self):
        if self.cursor is not None: self.message.set('回放只读，请先回到最新。'); return
        options=[a for s in range(4) for a in self.game.legal_actions(s)]
        if not options: self._stop(); return
        action=next((a for a in options if a.kind in ('ron','tsumo')),None)
        action=action or next((a for a in options if a.kind=='pass'),None)
        if action is None:
            seat=self.game.actor; p=self.game.observe(seat)
            drawn=p.draw_context.get('tile_id') if isinstance(p.draw_context,dict) else None
            action=next((a for a in options if a.kind=='discard' and a.tile_ids==(drawn,)),None)
            action=action or next(a for a in options if a.kind=='discard')
        self.game.submit(action); self.next_operator()
        if self.game.phase=='ended': self._stop()

    def toggle_auto(self):
        if self.running: self._stop(); return
        if self.cursor is not None: self.message.set('请先回到最新。'); return
        self.running=True; self.auto_button.configure(text='停止自动测试'); self._tick()

    def _tick(self):
        self.timer=None
        if not self.running: return
        self.step()
        if self.running: self.timer=self.root.after(150,self._tick)

    def change_view(self): self._stop(); self.cursor=None; self.refresh()
    def latest(self): self.cursor=None; self.next_operator()

    def select_history(self,event=None):
        selected=self.history.selection()
        if selected:
            cursor=int(selected[0])
            if cursor==self.cursor or self.cursor is None and cursor==len(self.game.record(self.observer.get()).events): return
            self._stop(); self.cursor=cursor; self.refresh()

    def undo(self):
        if self.cursor is not None: self.message.set('回放期间不能撤销。'); return
        if not self.game.command_count: return
        self._stop(); self.game=replay_simulation(self.game,self.game.command_count-1); self.next_operator()

    def save(self):
        try:
            if Path(self.path.get()).exists() and not self.overwrite.get(): raise ValueError('文件已存在，请勾选允许覆盖。')
            save_simulation(self.game,self.path.get()); self.message.set('已保存全知模拟存档；不要作为分析或训练输入。')
        except (ValueError,OSError) as error: self.message.set(str(error))

    def load(self):
        try:
            game=load_simulation(self.path.get()); self._stop(); self.game=game; self.cursor=None; self.next_operator()
            self.message.set('已加载并重放验证。运行规则来自存档；上方设置仅用于新局。')
        except (ValueError,OSError) as error: self.message.set(str(error))


def main():
    root=tk.Tk(); SimulatorApp(root); root.mainloop()
