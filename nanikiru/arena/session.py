"""A bot receives only AnalysisRequest; the host alone owns the simulator.

Custom bots implement callable(request) -> Action. Local response IDs and opaque
tile IDs are translated only after version and observer permission checks.
"""

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import re

from ..analysis import analyze
from ..analysis.fixed_policy import Parameters, FORMULA_VERSION
from ..analysis.output import debug_json
from ..contracts import Action, request_from_dict
from ..processing.analysis_input import build_analysis_request
from ..rules.config import tenhou_hanchan
from ..rules.visible_actions import additional_actions
from ..simulation import Simulator

MODES = ('人工', 'v1 牌效', '默认 v2', '拟合 v2')


def load_weights(path):
    """Require the fitted report and A rules, not an unlabelled parameter blob."""
    path=Path(path)
    weights=json.loads(path.read_text(encoding='utf-8-sig'))
    report=json.loads(path.with_name('report.json').read_text(encoding='utf-8-sig'))
    if report.get('formula_version')!=FORMULA_VERSION:
        raise ValueError('权重公式版本不匹配；需重新拟合，旧基线请使用保存的旧代码')
    from dataclasses import asdict
    if (report.get('weights')!=weights or report.get('dataset',{}).get('rules')!=asdict(tenhou_hanchan())
            or report.get('dataset',{}).get('status')!='complete'):
        raise ValueError('权重与报告不匹配，或不是已完成的 A 规训练')
    Parameters(**weights)
    return weights


@dataclass
class Turn:
    request: object
    version: str
    tile_ids: dict


@dataclass
class Decision:
    action: Action | None
    result: object
    reason: str


def inspect_turn(request):
    """Default analyzer bot; v1 adds explicit win/pass rules, never calls melds."""
    result=analyze(request)
    if request.method=='minimal-shape-v1':
        extra=additional_actions(request)
        win=next((c.action for c in extra if c.legality=='allowed' and c.action.kind in ('ron','tsumo')),None)
        if win:return Decision(win,result,'v1 辅助规则：合法和牌优先')
    choices=result.recommendations.get('overall',())
    if choices:return Decision(choices[0],result,'分析器综合推荐；并列时固定选首项')
    if request.method=='minimal-shape-v1':
        passed=next((c.action for c in extra if c.legality=='allowed' and c.action.kind=='pass'),None)
        if passed:return Decision(passed,result,'v1 不评价鸣牌：过')
    return Decision(None,result,'没有确定可执行推荐，暂停并等待人工处理')


class Session:
    def __init__(self, game=None):
        self.game=game if game is not None else Simulator(rules=tenhou_hanchan())

    def acting_seat(self, preferred=None):
        # Resolve all simultaneous responders before advancing; human seats can
        # be preferred by the UI without revealing other players' options.
        seats=([preferred] if preferred is not None else [])+list(range(4))
        return next((s for s in seats if self.game.legal_actions(s)),None)

    def turn(self, seat, mode='默认 v2', weights=None):
        if mode not in MODES:raise ValueError('Unknown controller')
        if mode=='拟合 v2' and (weights is None or self.game.rules!=tenhou_hanchan()):
            raise ValueError('拟合 v2 需要 A 规和有效训练权重')
        request=build_analysis_request(self.game.record(seat),source_version=self.game.version,
            method='minimal-shape-v1' if mode=='v1 牌效' else 'fixed-v2',
            method_options=deepcopy(weights) if mode=='拟合 v2' else {})
        # Simulator entity IDs encode wall positions. Never expose these to a
        # plug-in bot: use fresh opaque aliases, valid for this request only.
        aliases={}
        def opaque(value):
            if isinstance(value,str) and re.fullmatch(r't\d+',value):
                if value not in aliases:aliases[value]=f'v{len(aliases)}'
                return aliases[value]
            if isinstance(value,dict):return {k:opaque(v) for k,v in value.items()}
            if isinstance(value,list):return [opaque(v) for v in value]
            return value
        data=opaque(json.loads(debug_json(request,indent=None)))
        return Turn(request_from_dict(data),self.game.version,{v:k for k,v in aliases.items()})

    def submit(self, turn, action):
        if turn.version!=self.game.version:raise ValueError('局面已改变，拒绝执行旧建议')
        p=turn.request.position
        if not isinstance(action,Action) or action.actor!=p.observer_seat:raise ValueError('机器人不能操作其他玩家')
        expected=p.pending['trigger_event'] if isinstance(p.pending,dict) else None
        if action.target_id is not None and action.target_id!=expected:raise ValueError('响应不属于当前事件')
        if expected is not None and action.kind in ('pass','ron','chi','pon','open_kan') and action.target_id!=expected:
            raise ValueError('响应缺少当前目标事件')
        try:ids=tuple(turn.tile_ids[t] for t in action.tile_ids)
        except KeyError as error:raise ValueError('动作引用不可见牌') from error
        match=next((a for a in self.game.legal_actions(action.actor) if a.kind==action.kind
                    and set(a.tile_ids)==set(ids)),None)
        if match is None:raise ValueError('分析动作未通过引擎合法性复核；未执行替代动作')
        self.game.submit(match)
        return match

    def step(self, mode='v1 牌效', weights=None, bot=None):
        seat=self.acting_seat()
        if seat is None:return None
        turn=self.turn(seat,mode,weights)
        if bot is not None:
            action=bot(deepcopy(turn.request))
        else:
            decision=inspect_turn(turn.request)
            action=decision.action
        if action is None:raise ValueError('机器人没有可执行建议')
        self.submit(turn,action)
        return action
