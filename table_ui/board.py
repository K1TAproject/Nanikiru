"""Four-sided Canvas table using the project's original vector tile artwork."""

import math

from nanikiru.models import UNKNOWN
from nanikiru.tiles import index
from test_ui.tiles import draw_face

FELT='#174e48'


class Painter:
    def __init__(self, canvas, origin, angle, scale):
        self.canvas,self.origin,self.angle,self.scale=canvas,origin,angle,scale

    def points(self, coords):
        c,s=round(math.cos(math.radians(self.angle))),round(math.sin(math.radians(self.angle)))
        result=[]
        for x,y in zip(coords[::2],coords[1::2]):
            result.extend((self.origin[0]+(x*c-y*s)*self.scale,self.origin[1]+(x*s+y*c)*self.scale))
        return result

    def draw(self, kind, coords, options):
        coords=self.points(coords)
        if kind=='text':
            options['angle']=-self.angle
            if 'font' in options:
                f=options['font'];options['font']=(f[0],max(6,round(f[1]*self.scale)),*f[2:])
        if 'width' in options:options['width']=max(1,options['width']*self.scale)
        return getattr(self.canvas,'create_'+kind)(*coords,**options)

    def create_rectangle(self,*coords,**options):return self.draw('rectangle',coords,options)
    def create_oval(self,*coords,**options):return self.draw('oval',coords,options)
    def create_polygon(self,*coords,**options):return self.draw('polygon',coords,options)
    def create_line(self,*coords,**options):return self.draw('line',coords,options)
    def create_text(self,*coords,**options):return self.draw('text',coords,options)


def draw_table(canvas, position, history, *, modes, selected=None, recommended=()):
    canvas.delete('all')
    width,height=canvas.winfo_width(),canvas.winfo_height()
    scale=min(width/900,height/760)
    ox,oy=(width-900*scale)/2,(height-760*scale)/2
    def painter(origin=(0,0),angle=0):
        return Painter(canvas,(ox+origin[0]*scale,oy+origin[1]*scale),angle,scale)
    base=painter()
    base.create_rectangle(8,8,892,752,fill='#103d39',outline='#37766a',width=3)
    base.create_rectangle(24,24,876,736,fill=FELT,outline='#205f54')
    base.create_rectangle(350,310,550,425,fill='#102e31',outline='#c6ab70',width=2)
    wind={'east':'东','south':'南','west':'西','north':'北'}.get(position.round_wind,'?')
    base.create_text(450,332,text=f'{wind} {position.hand_number} 局  ·  {position.honba} 本场',fill='#f3e6bf',font=('Microsoft YaHei',14,'bold'))
    base.create_text(450,357,text=f'余牌 {position.remaining_draws}   供托 {position.riichi_sticks}',fill='#d2e5dc',font=('Microsoft YaHei',11))
    dora=[t for t in position.tiles if t.zone=='dora']
    base.create_text(365,392,text='指示',fill='#a3c7bc',font=('Microsoft YaHei',9))
    for i,t in enumerate(dora):draw_face(base,t.code,391+i*29,376,24,34)
    hitboxes=[]
    observer=position.observer_seat
    anchors=((450,682),(820,375),(450,68),(80,375))
    angles=(0,270,180,90)
    drawn=position.draw_context.get('tile_id') if isinstance(position.draw_context,dict) else None
    called={t.id for t in position.tiles if t.zone=='meld'}
    ending=next((e.data['fact']['result'] for e in reversed(history) if e.kind=='sim_ended'),None)
    revealed={h['seat']:h for h in ending['details'].get('hands',[])} if ending else {}
    for relative in range(4):
        seat=(observer+relative)%4
        pen=painter(anchors[relative],angles[relative])
        ready=any(e.kind=='sim_riichi_accepted' and e.actor==seat for e in history)
        player=next(p for p in position.players if p.seat==seat)
        active=position.actor==seat
        label=f'{seat} 家 · {player.score} 点'+(' · 庄' if seat==position.dealer_seat else '')+(' · 立直' if ready else '')
        pen.create_text(0,-29,text=label,fill='#ffe291' if active else '#f2f2df',font=('Microsoft YaHei',12,'bold'))
        pen.create_text(0,59,text=modes[seat],fill='#a3c7bc',font=('Microsoft YaHei',9))
        hand=sorted([t for t in position.tiles if t.zone=='hand' and t.seat==seat],key=lambda t:(t.id==drawn,index(t.code),t.code))
        if seat==observer:
            start=-len(hand)*18
            for i,t in enumerate(hand):
                x=start+i*36+(12 if t.id==drawn else 0);y=-7 if t.id==selected else 0
                draw_face(pen,t.code,x,y,32,46)
                if t.id in recommended or t.id==selected:
                    pen.create_rectangle(x-2,y-2,x+34,y+49,outline='#ffd36b' if t.id in recommended else '#69cdf4',width=2)
                coords=pen.points((x,y,x+34,y+49))
                hitboxes.append((*coords,t.id))
        elif seat in revealed:
            codes=list(revealed[seat]['concealed'])
            if ending['details']['source'] is not None:codes.append(revealed[seat]['win_tile'])
            for i,code in enumerate(sorted(codes,key=index)):
                draw_face(pen,code,-len(codes)*13+i*26,0,23,33)
        else:
            for i in range(player.untracked_hand_count):
                x=-player.untracked_hand_count*10+i*20
                pen.create_rectangle(x,0,x+17,28,fill='#80aaa1',outline='#d1d9c3',width=1)
                pen.create_rectangle(x+3,4,x+14,24,fill='#365f5c',outline='')
        discards=[e.data['fact'] for e in history if e.kind=='sim_discard' and e.actor==seat]
        rows=max(1,(len(discards)+5)//6);th=min(34,145/rows-3);tw=th*.7
        for i,fact in enumerate(discards):
            x=-3*(tw+3)+(i%6)*(tw+3);y=-235+(i//6)*(th+3)
            draw_face(pen,fact['code'],x,y,tw,th)
            if fact.get('riichi'):pen.create_rectangle(x-1,y-1,x+tw+1,y+th+1,outline='#ffbd53',width=2)
            if fact['tile'] in called:pen.create_line(x,y,x+tw,y+th,fill='#dd655c',width=2)
        groups=[]
        for gid,group in position.groups.items():
            members=[t for t in position.tiles if t.group_id==gid and t.seat==seat]
            if members:groups.append((group,members))
        x=-sum(len(ts)*23+12 for _,ts in groups)/2
        for group,members in groups:
            for t in sorted(members,key=lambda t:index(t.code)):
                draw_face(pen,t.code,x,-77,20,29)
                if t.id==group['called_tile_id']:pen.create_line(x,-45,x+20,-45,fill='#ffcf73',width=2)
                x+=23
            x+=12
    return hitboxes
