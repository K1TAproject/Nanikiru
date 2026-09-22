"""Versioned, descriptive comparisons of saved pre-action analyses, never a policy."""

from copy import deepcopy

from .efficiency import visible_counts
from .models import Tile
from .tenpai import ANALYSIS_VERSION

COMPARISON_VERSION = "tenpai-comparison-v1"


def candidate_view(record, discard, decision_record=None):
    """Project one saved legal candidate; no rules or scoring are recalculated."""
    observation = record['observation']
    visible_counts(observation)  # Reject privileged input even for display projections.
    basic = next((c for c in record['analysis']['candidates'] if c['discard'] == discard), None)
    if basic is None:
        raise ValueError('Discard is not in the saved legal candidates')
    own = observation['players'][record['player']]
    hand = deepcopy(own['hand']['known_tiles'])
    if own['hand']['drawn_tile']:
        hand.append(deepcopy(own['hand']['drawn_tile']['tile']))
    hand.remove(discard['tile'])
    hand.sort(key=lambda t: ('mpsz'.index(t['suit']), t['rank'], not t['is_red']))
    analysis = record.get('tenpai')
    tenpai = None
    if analysis and analysis.get('version') == ANALYSIS_VERSION:
        tenpai = next((c for c in analysis['candidates'] if c['discard'] == discard), None)
    defense = None
    mode = None
    if decision_record is not None:
        if (decision_record['action_index'] != record['action_index']
                or decision_record['player'] != record['player']
                or decision_record['observation'] != observation
                or decision_record['actual_action'] != record['actual_action']):
            raise ValueError('Defense record must belong to the same pre-action observation')
        saved = decision_record.get('defense')
        if saved:
            mode = saved['mode']
            defense = next((c for c in saved['candidates'] if all(c['action'].get(k) == v for k, v in discard.items())), None)
    return deepcopy({'discard': discard, 'basic': basic, 'hand': hand, 'melds': own['melds'],
                     'tenpai': tenpai, 'limited': not analysis or analysis.get('limited', True) or tenpai is None,
                     'defense': defense, 'mode': mode})


def compare_candidates(record, alternative_discard, decision_record=None):
    """Actual vs selected alternative, in that order; no recommendation or ranking."""
    actual = candidate_view(record, record['comparison']['selected']['discard'], decision_record)
    alternative = candidate_view(record, alternative_discard, decision_record)
    left, right = actual['tenpai'], alternative['tenpai']
    messages, differences = [], {}
    if record['comparison']['forced']:
        messages.append('仅一种合法弃牌，属于强制选择。')
    if actual['discard'] == alternative['discard']:
        messages.append('当前对照为同一动作，请选择其他候选（如有）。')
    if actual['basic']['is_best'] and alternative['basic']['is_best']:
        messages.append('两者均为基础牌效最优／并列最优，不表示综合等价。')
    if actual['basic']['shanten'] != 0 or alternative['basic']['shanten'] != 0:
        status = 'not_applicable'
        messages.append('包含未听牌候选：不适用听牌比较，仍展示原基础牌效。')
    elif left is None or right is None:
        status = 'limited'
        messages.append('信息不足：缺少受支持的历史听牌分析，不能补猜。')
    else:
        status = 'limited' if actual['limited'] or alternative['limited'] else 'descriptive'
        for key, label in (('shape_wait_types', '理论等待种数'), ('available_wait_types', '尚有未见的等待种数'),
                           ('shape_unseen', '牌形等待未见枚数')):
            differences[key] = right[key] - left[key]
            messages.append(f'{label}：实际 {left[key]} / 对照 {right[key]}。')
        for kind, label in (('ron', '普通荣和'), ('tsumo', '普通自摸')):
            differences[kind + '_yaku_unseen'] = right['yaku_unseen'][kind] - left['yaku_unseen'][kind]
            messages.append(f'{label}有役未见：实际 {left["yaku_unseen"][kind]} / 对照 {right["yaku_unseen"][kind]} 枚（未扣荣和限制）。')
        if left['ron_state'] != right['ron_state'] or left['ron_restrictions'] != right['ron_restrictions']:
            messages.append('荣和限制不同，请分别查看原因及解除临时振听后的条件。')
        messages.append('取舍说明：需结合逐张等待及其条件打点理解差异；不取最大值或平均值评定整体优劣，不推荐综合最优。')
        if status == 'limited':
            messages.append('信息不足：至少一方有历史状态未知，已知指标仍可对照。')
    source = record.get('tenpai', {})
    tenpai_faces = {str(Tile(**c['discard']['tile'])) for c in record['analysis']['candidates'] if c['shanten'] == 0}
    if len(tenpai_faces) == 1:
        messages.append('仅一种听牌弃牌牌面；同牌手切／摸切仍保留动作身份。')
    return {'version': COMPARISON_VERSION, 'source_version': source.get('version'),
            'assumptions': deepcopy(source.get('assumptions', {})), 'status': status,
            'actual': actual, 'alternative': alternative, 'differences': differences, 'messages': messages}
