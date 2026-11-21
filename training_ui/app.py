"""Read-only training monitor; no strategy dependencies or task mutation."""
import json
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog

STAGES={'selected':'已划分','preparing':'预处理','preparing_with_errors':'预处理中，有失败分片',
        'importing':'导入牌谱','extracting':'计算特征','merging':'合并缓存','fitting':'搜索权重',
        'evaluating':'最终评测','complete':'已完成','failed':'失败','pending':'未开始','incomplete':'未完成'}


def read_json(path, default=None):
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {} if default is None else default


def display_time(value):
    try:return datetime.fromisoformat(value).astimezone().strftime('%m-%d %H:%M:%S')
    except (ValueError,TypeError):return value


def snapshot(root):
    plan=read_json(root/'experiment.json');status=read_json(root/'status.json');chunks=[]
    for i in range(len(plan.get('chunks',[]))):
        cache=read_json(root/f'cache-{i:03d}/manifest.json')
        imported=read_json(root/f'import-{i:03d}/import-report.json')
        live=read_json(root/f'chunk-{i:03d}.json')
        state=live.get('stage','pending');rows=live.get('rows',0)
        if cache.get('status')=='complete':state='complete';rows=sum(cache.get('rows',{}).values())
        elif not live and imported.get('status')=='incomplete':state='failed' if status.get('stage')=='failed' else 'incomplete'
        elif not live and cache:state='incomplete'
        elif not live and imported:state='incomplete'
        chunks.append(dict(index=i,state=state,rows=rows,games=live.get('games',imported.get('games','—')),
                           updated=live.get('updated_at','旧任务未记录心跳'),error=live.get('error','')))
    progress=[]
    try:
        for line in (root/'fit-progress.jsonl').read_text(encoding='utf-8').splitlines():
            try:progress.append(json.loads(line))
            except ValueError:pass  # An in-progress final line is not an event yet.
    except OSError:pass
    return dict(status=status,chunks=chunks,progress=progress,
                completed=sum(c['state']=='complete' for c in chunks),failed=sum(c['state']=='failed' for c in chunks),
                rows=sum(c['rows'] for c in chunks),total=len(chunks))


class Monitor:
    def __init__(self,root,directory):
        self.root=root;self.directory=Path(directory);self.timer=None
        root.title('Nanikiru · 训练进度（只读）');root.geometry('1100x820');root.minsize(900,650)
        bar=ttk.Frame(root,padding=8);bar.pack(fill='x')
        self.path=tk.StringVar(value=str(self.directory));ttk.Entry(bar,textvariable=self.path).pack(side='left',fill='x',expand=True)
        ttk.Button(bar,text='选择实验目录',command=self.select).pack(side='left',padx=5)
        self.summary=tk.StringVar();ttk.Label(root,textvariable=self.summary,padding=8,wraplength=1050).pack(fill='x')
        self.bar=ttk.Progressbar(root,maximum=40);self.bar.pack(fill='x',padx=10)
        self.activity=tk.StringVar();ttk.Label(root,textvariable=self.activity,padding=8,wraplength=1050).pack(fill='x')
        table=ttk.Frame(root);table.pack(fill='both',expand=True,padx=10)
        self.table=ttk.Treeview(table,columns=('chunk','state','games','rows','updated'),show='headings',height=12)
        for name,title,width in [('chunk','分片',60),('state','状态',160),('games','导入场数',80),('rows','特征行数',100),('updated','最近进度更新时间',330)]:
            self.table.heading(name,text=title);self.table.column(name,width=width)
        scroll=ttk.Scrollbar(table,command=self.table.yview);self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        self.chart=tk.Canvas(root,height=180,bg='white',highlightthickness=1,highlightbackground='#ddd');self.chart.pack(fill='x',padx=10,pady=6)
        self.detail=tk.Text(root,height=7,wrap='word');self.detail.pack(fill='x',padx=10,pady=6)
        ttk.Label(root,text='每 2 秒自动刷新；关闭本窗口不会停止任务。分片状态来自落盘文件，不以进度文件的旧计数代替实际成果。',padding=6).pack(fill='x')
        self.refresh()
        root.protocol('WM_DELETE_WINDOW',self.close)

    def select(self):
        folder=filedialog.askdirectory(initialdir=self.directory)
        if folder:self.path.set(folder)

    def refresh(self):
        self.directory=Path(self.path.get());data=snapshot(self.directory);s=data['status']
        stage=STAGES.get(s.get('stage'),s.get('stage','未找到任务状态'))
        self.summary.set(f"{stage}｜预处理完成 {data['completed']} / {data['total']} 批；失败 {data['failed']} 批；已记录特征 {data['rows']:,} 行")
        self.bar.configure(maximum=max(1,data['total']),value=data['completed'])
        latest=data['progress'][-1] if data['progress'] else {}
        now=datetime.now().strftime('%H:%M:%S')
        self.activity.set(f"界面刷新 {now}｜状态更新时间：{display_time(s.get('updated_at','旧任务未记录，不能据此证明存活'))}\n"
                          f"拟合：第 {latest.get('round','—')} / {latest.get('rounds','—')} 轮；参数 {latest.get('parameter','—')}；试值 {latest.get('value','—')}；训练损失 {latest.get('train_loss','—')}")
        for c in data['chunks']:
            key=str(c['index']);values=(c['index'],STAGES.get(c['state'],c['state']),c['games'],c['rows'],display_time(c['updated']))
            if self.table.exists(key):self.table.item(key,values=values)
            else:self.table.insert('', 'end',iid=key,values=values)
        for key in self.table.get_children():
            if int(key)>=data['total']:self.table.delete(key)
        errors=[s.get('error','')]+[f"分片 {c['index']}: {c['error']}" for c in data['chunks'] if c['error']]
        if s.get('failed_chunks'):errors.extend(str(e) for e in s['failed_chunks'])
        if s.get('completed_chunks')!=data['completed']:
            errors.append(f"原进度文件计数 {s.get('completed_chunks')} 与实际完成 {data['completed']} 不同；本窗口显示实际分片结果。")
        if not latest:errors.append('尚无拟合过程记录；预处理百分比不是整体训练完成百分比。')
        errors.append('缓存是否适用于新代码仍需训练入口的指纹检查；本窗口不绕过校验。')
        self.detail.configure(state='normal');self.detail.delete('1.0','end');self.detail.insert('1.0','\n'.join(e for e in errors if e));self.detail.configure(state='disabled')
        self.draw_chart(data['progress']);self.timer=self.root.after(2000,self.refresh)

    def draw_chart(self,events):
        self.chart.delete('all');points=[e for e in events if e.get('event') in ('baseline','round_complete')]
        if not points:
            self.chart.create_text(25,30,text='尚无损失曲线：进入权重拟合后显示训练集／验证集损失（不显示测试集选参曲线）。',anchor='w');return
        width=max(850,self.chart.winfo_width());height=180
        values=[p[k] for p in points for k in ('train_loss','validation_loss')];lo=min(values);span=max(values)-lo or 1
        for key,color,label in [('train_loss','#2563eb','训练损失'),('validation_loss','#d97706','验证损失')]:
            coords=[]
            for i,p in enumerate(points):
                x=65+i*(width-130)/max(1,len(points)-1);y=140-(p[key]-lo)/span*95;coords.extend((x,y))
                self.chart.create_oval(x-3,y-3,x+3,y+3,fill=color,outline=color)
                self.chart.create_text(x,y-12,text=f"{p[key]:.4f}",fill=color)
            if len(points)>1:self.chart.create_line(*coords,fill=color,width=2)
            self.chart.create_text(150 if key=='train_loss' else 340,16,text=label,fill=color)
        self.chart.create_text(width/2,165,text='初始参数 → 每轮结束；损失越低越好，非和牌率')

    def close(self):
        if self.timer:self.root.after_cancel(self.timer)
        self.root.destroy()
