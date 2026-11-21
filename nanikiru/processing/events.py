"""Strict payloads for the deliberately small manual event vocabulary."""

from ..models import Event, UNKNOWN, valid_seat
from ..tiles import validate_code

HALT_REASONS = ("chi", "pon", "kan", "riichi", "win", "draw_end", "other")


def validate_event(event):
    Event(event.id, event.kind, event.actor, event.target_id, event.data)
    fields = {
        "draw": {"tile_id", "code"},
        "discard": {"tile_id", "code", "discard_kind"},
        "close_response": set(),
        "halt": {"reason"},
    }
    if event.kind not in fields:
        raise ValueError("不支持此事件；请记录 halt 并停止推进")
    if not isinstance(event.data, dict) or set(event.data) != fields[event.kind]:
        raise ValueError("事件字段缺失或包含不支持的字段")
    if event.kind in ("draw", "discard"):
        if not valid_seat(event.actor) or event.target_id is not None:
            raise ValueError("摸打需要明确玩家，且不能引用响应事件")
        tile_id = event.data["tile_id"]
        if tile_id is not None and (not isinstance(tile_id, str) or not tile_id):
            raise ValueError("牌记录 ID 必须非空")
        if event.kind == "discard" and tile_id is None:
            raise ValueError("弃牌必须有实体记录 ID")
        if event.data["code"] is not UNKNOWN:
            validate_code(event.data["code"])
        if (event.kind == "discard" and event.data["discard_kind"] is not UNKNOWN
                and event.data["discard_kind"] not in ("hand", "tsumogiri")):
            raise ValueError("弃牌方式只能为 hand、tsumogiri 或 UNKNOWN")
    elif event.actor is not None:
        raise ValueError("系统确认事件不指定行为玩家")
    if event.kind == "halt":
        if event.target_id is not None or event.data["reason"] not in HALT_REASONS:
            raise ValueError("无效的暂停原因")
    if event.kind == "close_response" and event.target_id is None:
        raise ValueError("需要引用当前弃牌；中途接入缺失引用时使用 UNKNOWN")
