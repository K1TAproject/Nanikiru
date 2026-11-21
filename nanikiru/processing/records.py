"""Authoritative initial snapshot + events; derived snapshots are disposable."""

from copy import deepcopy
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from ..models import UNKNOWN, Event, PlayerInfo, RoundRecord, RoundSnapshot, TileRecord
from .progression import apply_event, validate_position
from ..rules.config import RuleConfig
from .observed_simulation import apply_observation

FILE_FORMAT = "nanikiru-manual-v1"


def replay(record, through=None):
    """Rebuild through N events (0 = entry snapshot); no inferred prehistory."""
    RoundRecord(record.initial_snapshot, record.events, record.coverage, record.schema_version)
    validate_position(record.initial_snapshot)
    if not isinstance(record.coverage, dict) or set(record.coverage) != {"from_start", "gaps"}:
        raise ValueError("记录必须提供 from_start 与 gaps")
    start = record.coverage["from_start"]
    if start is not UNKNOWN and type(start) is not bool:
        raise ValueError("from_start 必须为 bool 或 UNKNOWN")
    if not isinstance(record.coverage["gaps"], list):
        raise ValueError("gaps 必须为列表")
    if record.coverage["gaps"]:
        raise ValueError("本版不跨历史缺口推演；请从缺口后的确认快照新建中途记录")
    if through is None:
        through = len(record.events)
    if type(through) is not int or not 0 <= through <= len(record.events):
        raise ValueError("无效的回放位置")
    position = deepcopy(record.initial_snapshot)
    for event in record.events[:through]:
        position = apply_observation(position, event) if event.kind.startswith(("sim_", "tenhou_", "mjai_")) else apply_event(position, event)
    return position


def append_event(record, event):
    if record.events and event.id <= record.events[-1].id:
        raise ValueError("事件编号必须递增，不能重复应用")
    position = replay(record)
    apply_event(position, event)
    result = deepcopy(replace(record, events=(*record.events, event)))
    return result


def undo(record):
    if not record.events:
        raise ValueError("没有可撤销的事件")
    result = deepcopy(replace(record, events=record.events[:-1]))
    replay(result)
    return result


def _encode(value):
    if value is UNKNOWN:
        return {"$unknown": True}
    if isinstance(value, dict):
        if "$unknown" in value:
            raise ValueError("$unknown 是存档保留键")
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


def _decode(value):
    if isinstance(value, dict):
        if "$unknown" in value:
            if set(value) != {"$unknown"} or value["$unknown"] is not True:
                raise ValueError("未知值编码错误")
            return UNKNOWN
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def save_record(record, path):
    """Validate before writing; atomically replace a file in its own directory."""
    replay(record)
    data = json.dumps({"format": FILE_FORMAT, "record": _encode(asdict(record))},
                      ensure_ascii=False, indent=2, allow_nan=False)
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def load_record(path):
    """Only this new format; restore UNKNOWN identity and validate full replay."""
    try:
        envelope = json.loads(Path(path).read_text(encoding="utf-8"))
        if set(envelope) != {"format", "record"} or envelope["format"] != FILE_FORMAT:
            raise ValueError("仅支持 nanikiru-manual-v1，不兼容旧存档")
        data = _decode(envelope["record"])
        snapshot = data.pop("initial_snapshot")
        snapshot["rules"] = RuleConfig(**snapshot.get("rules", {}))
        snapshot["players"] = tuple(PlayerInfo(**p) for p in snapshot["players"])
        snapshot["tiles"] = tuple(TileRecord(**t) for t in snapshot["tiles"])
        if isinstance(snapshot["pending"], dict):
            snapshot["pending"]["responses"] = {int(k): v for k, v in snapshot["pending"]["responses"].items()}
        record = RoundRecord(initial_snapshot=RoundSnapshot(**snapshot),
                             events=tuple(Event(**e) for e in data.pop("events")), **data)
        replay(record)
        return record
    except (TypeError, KeyError, AttributeError) as error:
        raise ValueError("记录字段结构错误") from error


class Session:
    """A small owner of the record, current derived view, and revision token."""
    def __init__(self, record):
        self._commit(record)

    def _commit(self, record):
        position = replay(record)
        self._record, self._position = deepcopy(record), position
        self.state_version = uuid4().hex

    @property
    def record(self):
        return deepcopy(self._record)

    @property
    def position(self):
        return deepcopy(self._position)

    def append(self, kind, *, actor=None, target_id=None, data=None):
        event_id = self._record.events[-1].id + 1 if self._record.events else 1
        event = Event(event_id, kind, actor, target_id, {} if data is None else data)
        self._commit(append_event(self._record, event))

    def undo(self):
        self._commit(undo(self._record))

    def load(self, path):
        self._commit(load_record(path))

    def save(self, path):
        save_record(self._record, path)
