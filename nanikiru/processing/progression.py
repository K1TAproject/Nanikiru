"""Apply confirmed observations, not permission requests or simulated turns."""

from copy import deepcopy
from dataclasses import replace

from ..models import UNKNOWN, TileRecord, valid_seat
from .events import validate_event


def validate_position(position):
    """Reject known entry inconsistencies; explicitly unknown facts stay unknown."""
    position.validate()
    if position.phase in ("await_draw", "await_discard"):
        if isinstance(position.pending, dict) or position.actor is None:
            raise ValueError("主动摸打阶段不能同时存在响应窗口或无行动家")
    if position.phase == "await_draw" and isinstance(position.draw_context, dict):
        raise ValueError("待摸牌阶段仍持有摸牌上下文")
    if position.phase == "await_discard" and isinstance(position.draw_context, dict):
        if valid_seat(position.actor) and position.draw_context["player"] != position.actor:
            raise ValueError("摸牌上下文属于另一玩家")
    if position.phase == "await_response" and (valid_seat(position.actor) or position.pending is None):
        raise ValueError("响应阶段需要响应对象，不能指定主动摸打玩家")
    for player in position.players:
        seat = player.seat
        if (player.untracked_hand_count is UNKNOWN
                or position.completeness.get(f"hand:{seat}") is not True
                or position.completeness.get(f"groups:{seat}") is not True):
            continue
        groups = {t.group_id for t in position.tiles if t.zone == "meld" and t.seat == seat}
        count = player.untracked_hand_count + sum(t.zone == "hand" and t.seat == seat for t in position.tiles)
        expected = {13, 14} if position.phase is UNKNOWN or position.actor is UNKNOWN else {
            14 if position.phase == "await_discard" and position.actor == seat else 13}
        if count not in {n - 3 * len(groups) for n in expected}:
            raise ValueError("已确认完整的暗手数量与当前阶段矛盾")


def apply_event(position, event):
    """Return a new snapshot, or raise without mutating any caller input.

    Event ordering/deduplication belongs to records.append_event/replay.
    This checks transition/inventory consistency, not full action legality.
    """
    validate_position(position)
    validate_event(event)
    state = deepcopy(position)
    if event.kind == "halt":
        if state.phase == "ended":
            raise ValueError("牌局已结束")
        return replace(state, phase=UNKNOWN, actor=None)
    if event.kind == "close_response":
        pending = state.pending
        if state.phase != "await_response" or not isinstance(pending, dict) or pending["kind"] != "discard":
            raise ValueError("当前不是弃牌响应窗口")
        if event.target_id != pending["trigger_event"]:
            raise ValueError("响应确认引用了另一事件")
        source = pending["source_seat"]
        tile = next((t for t in state.tiles if t.id == pending["tile_id"]), None)
        if not valid_seat(source) or tile is None or tile.zone != "river" or tile.seat != source:
            raise ValueError("缺少一致的响应来源弃牌")
        if any(value not in ("pass", UNKNOWN) for value in pending["responses"].values()):
            raise ValueError("已有鸣牌/和牌响应，不能确认无人响应")
        # A closed observable window does not prove private passing/furiten facts.
        return replace(state, phase="await_draw", actor=(source + 1) % 4,
                       pending=None, draw_context=None)

    expected = "await_draw" if event.kind == "draw" else "await_discard"
    if state.phase != expected or state.actor != event.actor or state.pending is not None:
        raise ValueError("事件与当前行动阶段或玩家不一致")
    player = next(p for p in state.players if p.seat == event.actor)
    count = player.untracked_hand_count
    tiles = list(state.tiles)
    tile_id, code = event.data["tile_id"], event.data["code"]
    existing = next((t for t in tiles if t.id == tile_id), None)

    # Check known counts when group coverage is established; do not invent it.
    if count is not UNKNOWN and state.completeness.get(f"groups:{event.actor}") is True:
        groups = {t.group_id for t in tiles if t.zone == "meld" and t.seat == event.actor}
        total = count + sum(t.zone == "hand" and t.seat == event.actor for t in tiles)
        wanted = (13 if event.kind == "draw" else 14) - 3 * len(groups)
        if total != wanted:
            raise ValueError("当前暗手数量与摸打阶段不一致")

    if event.kind == "draw":
        if state.remaining_draws == 0:
            raise ValueError("已知余牌为零，停止并等待终局处理")
        if state.draw_context is not None:
            raise ValueError("待摸牌阶段仍有未清除或未知的摸牌上下文")
        if event.actor != state.observer_seat:
            if code is not UNKNOWN or tile_id is not None:
                raise ValueError("对手暗摸只记录数量，不能输入隐藏牌面或实体身份")
            count = UNKNOWN if count is UNKNOWN else count + 1
            draw_id = UNKNOWN
        else:
            if tile_id is None or existing is not None:
                raise ValueError("自己的摸牌需要新的实体 ID")
            tiles.append(TileRecord(tile_id, code, "hand", event.actor))
            draw_id = tile_id
        state.phase = "await_discard"
        state.draw_context = {"player": event.actor, "tile_id": draw_id, "source": "live"}
        if state.remaining_draws is not UNKNOWN:
            state.remaining_draws -= 1
    else:
        if existing is not None:
            if existing.zone != "hand" or existing.seat != event.actor:
                raise ValueError("弃牌必须引用该玩家当前暗手")
            if existing.code is not UNKNOWN and code != existing.code:
                raise ValueError("已知手牌牌面不能被弃牌事件改写")
            tiles[tiles.index(existing)] = replace(existing, code=code, zone="river")
        else:
            if count == 0:
                raise ValueError("没有未建档暗手可供公开")
            count = UNKNOWN if count is UNKNOWN else count - 1
            tiles.append(TileRecord(tile_id, code, "river", event.actor))
        draw = state.draw_context
        if isinstance(draw, dict) and draw["tile_id"] is not UNKNOWN:
            actual = "tsumogiri" if tile_id == draw["tile_id"] else "hand"
            if event.data["discard_kind"] is not UNKNOWN and event.data["discard_kind"] != actual:
                raise ValueError("手切/摸切标记与已知摸牌身份矛盾")
        state.phase, state.actor, state.draw_context = "await_response", None, None
        state.pending = {"trigger_event": event.id, "kind": "discard", "source_seat": event.actor,
                         "tile_id": tile_id, "responses": {}}
    state.tiles = tuple(tiles)
    state.players = tuple(replace(p, untracked_hand_count=count) if p.seat == player.seat else p for p in state.players)
    state.validate()
    return state
