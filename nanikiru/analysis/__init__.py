"""Public analysis queries; implementations live in focused submodules."""

from .basic import analyze_shanten, analyze_waits, analyze_discard_shapes, score_hand
from .dispatcher import analyze

__all__ = ["analyze", "analyze_shanten", "analyze_waits", "analyze_discard_shapes", "score_hand"]
