"""Extract a prefix of an already-observed manual record, not a full-state view."""

from copy import deepcopy
from dataclasses import replace

from ..contracts import AnalysisRequest, validate_entry_context
from ..models import UNKNOWN, valid_seat
from .records import replay


def extract_player_view(record, through=None, *, observer_seat=None):
    """Return independent (snapshot, visible history, coverage) at a prefix.

    Cannot switch observers or sanitize arbitrary omniscient simulator data.
    Unspecified entry metadata is rejected rather than passed to analysis.
    """
    observer = record.initial_snapshot.observer_seat
    if observer_seat is not None and (not valid_seat(observer_seat) or observer_seat != observer):
        raise ValueError("人工记录只包含原观察者视角，不能切换玩家")
    count = len(record.events) if through is None else through
    if type(count) is not int or not 0 <= count <= len(record.events):
        raise ValueError("无效的分析时点")
    # Never replay, forward, or inspect event payloads after the selected point.
    prefix = deepcopy(replace(record, events=record.events[:count]))
    validate_entry_context(prefix.initial_snapshot.initial_context)
    position = replay(prefix)
    if isinstance(position.draw_context, dict):
        position.draw_context = {k: position.draw_context[k] for k in ("player", "tile_id", "source")}
    position.groups = {key: {name: group[name] for name in ("kind", "called_tile_id", "source_seat")}
                       for key, group in position.groups.items()}
    keys = {"public_tiles", *(f"{kind}:{seat}" for kind in ("hand", "groups") for seat in range(4))}
    position.completeness = {k: v for k, v in position.completeness.items() if k in keys}
    if isinstance(position.pending, dict):
        pending = position.pending
        # Other players' private responses are not observer-visible facts.
        position.pending = {k: pending[k] for k in ("trigger_event", "kind", "source_seat", "tile_id")}
        response = pending["responses"].get(observer, UNKNOWN)
        if response not in (UNKNOWN, "pass", "chi", "pon", "kan", "open_kan", "ron"):
            raise ValueError("不支持的观察者响应内容")
        position.pending["responses"] = {observer: response} if observer in pending["responses"] else {}
    position.validate()
    return position, prefix.events, prefix.coverage


def build_analysis_request(record, *, source_version, through=None, observer_seat=None,
                           tasks=("shanten", "tenpai", "discard_shape", "recommend"),
                           method="minimal-shape-v1", objective="shape", method_options=None):
    """Build the standard request; the version identifies revision AND prefix."""
    if not isinstance(source_version, str) or not source_version:
        raise ValueError("需要记录修订版本")
    if not isinstance(tasks, (tuple, list)):
        raise ValueError("tasks 必须是任务序列")
    position, history, coverage = extract_player_view(record, through, observer_seat=observer_seat)
    return AnalysisRequest(f"{source_version}:{len(history)}", position, tuple(tasks), history,
                           coverage, objective, method, {} if method_options is None else deepcopy(method_options))
