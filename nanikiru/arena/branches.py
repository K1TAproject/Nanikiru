"""Rule-complete conditional successors for OFFLINE simulated worlds.

This is an environment tool, not a player policy. A world fixes hidden hands,
wall and ura: never rank live actions by reading these conditional outcomes.
Responses are enumerated, not averaged or chosen for the observer. Chance
probabilities and belief sampling belong to a separate, not-yet-built planner.
"""

from copy import deepcopy
from dataclasses import dataclass

from ..contracts import Metric
from ..models import valid_seat


@dataclass
class Successor:
    actions: tuple
    game: object
    scope: str = 'conditional_simulated_world_not_policy_input'

    def decisions(self):
        """Retain ALL next choices, including wins, declines and special draws."""
        return {seat: actions for seat in range(4)
                if (actions := self.game.legal_actions(seat))}

    def observer_turn(self, seat):
        """Only this projection may be sent to a bot; IDs are opaque aliases."""
        from .session import Session
        return Session(self.game).turn(seat)

    def terminal_value(self, seat):
        """Lexicographic target only at a confirmed MATCH end, not a hand end."""
        if not valid_seat(seat):
            raise ValueError('Invalid observer')
        result = self.game.result
        match = (result or {}).get('match', {})
        if not match.get('ended'):
            return Metric('unavailable', details={'reason': 'match_continuation_required'})
        rank = match['ranking'].index(seat) + 1
        points = match.get('result_points')
        return Metric('conditional', {
            'rank_value': -rank,
            'secondary_value': points[rank-1] if points is not None else result['scores'][seat],
            'secondary_unit': 'result_points' if points is not None else 'final_points',
            'final_points': result['scores'][seat],
        }, 'exact', {'scope': self.scope, 'ordering': 'rank_then_secondary'})


def successors(game, action):
    """Yield every response combination after action, stopping at next turn/end.

Lazy enumeration has no silent pruning or fixed-policy pass/win shortcuts.
All response actors' choices remain conditional environment outcomes, NOT
choices controlled by the root player. Caller must exhaust the iterator to
claim response coverage. Fixed-world draws, dora and settlement reuse engine.
Input state is unchanged, including when an illegal action raises.
"""
    branch = deepcopy(game)
    branch.submit(action)

    def resolve(current, path):
        if current.phase not in ('await_response', 'await_kan_response'):
            yield Successor(path, current)
            return
        # Responses are simultaneous. Enumeration order conveys no information
        # to any policy and never greedily resolves another player's choice.
        choices = next((current.legal_actions(s) for s in range(4)
                        if current.legal_actions(s)), ())
        if not choices:
            raise RuntimeError('Unresolved response window without legal responders')
        for reply in choices:
            child = deepcopy(current)
            child.submit(reply)
            yield from resolve(child, (*path, reply))

    yield from resolve(branch, (action,))
