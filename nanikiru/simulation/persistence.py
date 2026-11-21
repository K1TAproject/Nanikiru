"""Full private simulation replay files; not player observations or training inputs."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

from ..contracts import Action
from ..rules.config import RuleConfig
from .engine import Simulator

FORMAT = "nanikiru-simulation-v1"


def replay_simulation(game, through=None):
    count = len(game._commands) if through is None else through
    if type(count) is not int or not 0 <= count <= len(game._commands):
        raise ValueError("Invalid command cursor")
    initial = dict(game._initial)
    initial["rules"] = RuleConfig(**initial["rules"])
    rebuilt = Simulator(**initial)
    for action in game._commands[:count]:
        rebuilt.submit(action)
    return rebuilt


def save_simulation(game, path):
    payload = dict(format=FORMAT, initial=game._initial, commands=[asdict(a) for a in game._commands])
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    path, temporary = Path(path), None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name+".", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def load_simulation(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if set(data) != {"format", "initial", "commands"} or data["format"] != FORMAT:
            raise ValueError("Only nanikiru-simulation-v1 is supported")
        initial = data["initial"]
        initial["rules"] = RuleConfig(**initial["rules"])
        game = Simulator(**initial)
        for raw in data["commands"]:
            raw["tile_ids"] = tuple(raw["tile_ids"])
            game.submit(Action(**raw))
        return game
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Invalid simulation file") from error
