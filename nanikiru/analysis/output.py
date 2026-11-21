"""Pure output formatting, not another calculation path or persistence format."""

from dataclasses import asdict, is_dataclass
import json

from ..models import UNKNOWN


def debug_json(value, *, indent=2):
    """Serialize the actual request/result; UNKNOWN remains distinct from None."""
    def encode(item):
        if item is UNKNOWN:
            return {"$unknown": True}
        if is_dataclass(item):
            return encode(asdict(item))
        if isinstance(item, dict):
            return {str(k): encode(v) for k, v in item.items()}
        if isinstance(item, (tuple, list)):
            return [encode(v) for v in item]
        return item
    return json.dumps(encode(value), ensure_ascii=False, indent=indent, allow_nan=False)


def require_current_result(result, state_version, method):
    if result.state_version != state_version or result.method != method:
        raise ValueError("分析结果属于旧时点或另一方法，不能展示为当前结果")
    result.__post_init__()


def summarize_result(request, result, selected_tile_id=None):
    """Render only supplied result values; never call a calculation primitive."""
    require_current_result(result, request.state_version, request.method)
    lines = []
    metric = result.situation.get("shanten")
    if metric is not None:
        lines.append(f"当前牌形：{metric.value if metric.value is not None else '未知'} 向听")
    selected = next((c for c in result.candidates if c.action.kind=='discard' and c.action.tile_ids == (selected_tile_id,)), None)
    if selected is not None:
        s, u = selected.metrics["shanten"].value, selected.metrics["ukeire"].value
        lines.append(f"所选弃牌后：{'未知' if s is None else s} 向听{'（听牌）' if s == 0 else ''}；"
                     f"未见进张：{'未知（信息不全）' if u is None else str(u)+' 枚'}")
        if "policy_score" in selected.metrics:
            lines.append(f"综合分：{selected.metrics['policy_score'].value:.3f}；危险指数：{selected.metrics['danger'].value:.3f}（非概率）")
            value = selected.metrics['conditional_value']
            lines.append(f"条件自摸打点：{round(value.value)} 点" if value.value is not None else f"条件打点：{value.status}")
    waiting = result.situation.get("waits")
    if waiting is not None and waiting.status == "available":
        lines.append("理论等待：" + ("、".join(x["tile"] for x in waiting.value) or "无"))
    ranking = result.situation.get("discard_ranking")
    if ranking is not None:
        if ranking.value:
            by_id = {t.id: t.code for t in request.position.tiles}
            codes = list(dict.fromkeys(by_id[a.tile_ids[0]] for a in ranking.value[0]))
            label = "弃牌比较：" if result.method == "fixed-v2" else ("牌效推荐：" if result.recommendations else "条件牌效偏好：")
            lines.append(label + "、".join(codes)
                         + ("（仅比较向听）" if ranking.details.get("missing_fields")
                            else "（同分并列）" if len(ranking.value[0]) > 1 else ""))
        else:
            lines.append(f"排序：{ranking.status} / {ranking.details.get('reason', ranking.details.get('missing_fields', ''))}")
    if not lines:
        lines.append("本次无可展示数值；请查看结构化结果中的状态与原因。")
    if result.method == "fixed-v2":
        by_id = {t.id: t.code for t in request.position.tiles}
        names={'discard':'切牌','riichi':'立直切牌','chi':'吃','pon':'碰','open_kan':'大明杠',
               'closed_kan':'暗杠','added_kan':'加杠','pass':'不鸣／不和','ron':'荣和','tsumo':'自摸'}
        def describe(action):
            return names.get(action.kind,action.kind)+' '+ '/'.join(str(by_id[t]) for t in action.tile_ids)
        actions=result.situation.get('action_ranking')
        if actions and actions.value:
            executable=result.recommendations.get('overall',())
            lines.append(('动作推荐：' if executable else '条件动作偏好：')+
                         '、'.join(dict.fromkeys(describe(a) for a in (executable or actions.value[0]))))
            best=next(c for c in result.candidates if c.action==(executable or actions.value[0])[0])
            route=best.metrics.get('routes')
            if route and route.value:
                name=route.value['preferred']
                title={'regular':'一般形','seven_pairs':'七对子','orphans':'国士无双',
                       'menzen_riichi':'门前立直','tanyao':'断幺九','all_triplets':'对对和',
                       'half_flush:m':'万子混一色','half_flush:p':'筒子混一色','half_flush:s':'索子混一色'}.get(name,name)
                if name and name.startswith('yakuhai:'):title='役牌 '+name.split(':')[1]
                lines.append('役种潜力参考：'+str(title)+'（粗略评分，未确定组牌路线）')
            branch=best.metrics.get('action_evaluation')
            if branch and 'follow_up_discard' in branch.value:
                lines.append('鸣牌后计划：'+describe(branch.value['follow_up_discard']))
        for strategy, title in (("attack", "进攻偏好"), ("defense", "防守偏好")):
            metric = result.situation.get(strategy+"_action_ranking")
            if metric and metric.value:
                lines.append(title+"："+"、".join(dict.fromkeys(describe(a) for a in metric.value[0])))
        lines.append("v2 为人工权重启发式；分数、危险指数不是和牌率或放铳率。")
        if not result.recommendations: lines.append("当前仅条件偏好，无已确认可执行推荐。")
    else:
        lines.append("合法弃牌推荐仅比较牌效，尚未评价防守与打点。" if result.recommendations
                     else "牌效偏好不等于行动许可；当前无可执行推荐。")
    return "\n".join(lines)
