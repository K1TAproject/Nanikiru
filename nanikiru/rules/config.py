"""One immutable, serializable ruleset shared by simulation and observation."""

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class RuleConfig:
    open_tanyao: bool = True
    kan_dora: bool = True
    ura_dora: bool = True
    kan_ura: bool = True
    aka_dora: bool = True
    forbid_kuikae: bool = True
    multiple_ron: bool = True
    ippatsu: bool = True
    bankruptcy: bool = True
    kiriage: bool = False
    kazoe_yakuman: bool = True
    double_yakuman: bool = False
    nagashi_mangan: bool = True
    abortive_draws: bool = True
    pao: bool = True
    match_length: str = "east"
    starting_points: int = 25000
    triple_ron_draw: bool = False
    honba_head_only: bool = False
    pao_all_yakuman: bool = False
    kokushi_ankan_ron: bool = True
    added_kan_ippatsu: bool = False
    formal_dead_waits: bool = False
    tenhou_match_end: bool = False

    def __post_init__(self):
        for item in fields(self):
            if item.name not in ("match_length", "starting_points") and type(getattr(self, item.name)) is not bool:
                raise ValueError(f"{item.name} must be bool")
        if self.match_length not in ("east", "south"):
            raise ValueError("match_length must be east/south")
        if type(self.starting_points) is not int or self.starting_points < 1000:
            raise ValueError("starting_points must be at least 1000")


TENHOU_HANCHAN = 'tenhou-4p-hanchan-2026-v1'


def tenhou_hanchan():
    """Ranked four-player kuitan/aka hanchan; see docs/tenhou-rules.md."""
    return RuleConfig(match_length='south', triple_ron_draw=True, honba_head_only=True,
                      pao_all_yakuman=True, kokushi_ankan_ron=False, added_kan_ippatsu=True,
                      formal_dead_waits=True, tenhou_match_end=True)
