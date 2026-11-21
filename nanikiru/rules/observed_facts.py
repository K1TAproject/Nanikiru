"""Read confirmed visible facts across adapters without replaying state."""

from ..models import UNKNOWN


def event_fact(event):
    if event.kind.startswith(('sim_', 'tenhou_', 'mjai_', 'observed_')):
        return event.kind.split('_', 1)[1], event.data['fact']
    return event.kind, event.data


def interrupts_ippatsu(kind, fact, rules):
    if kind == 'kan_declared':
        return not (rules.added_kan_ippatsu and fact.get('kan_kind') == 'added_kan')
    return kind in ('chi', 'pon', 'open_kan', 'closed_kan', 'added_kan')


def complete_history(history, coverage):
    return (coverage.get('from_start') is True and coverage.get('gaps') == []
            and bool(history) and all(e.kind.startswith(('sim_', 'tenhou_', 'mjai_', 'observed_')) for e in history))


def riichi_status(position, history, seat, coverage=None):
    baseline = position.initial_context.get('riichi', [UNKNOWN]*4)[seat]
    if any(e.actor == seat and event_fact(e)[0] == 'riichi_accepted' for e in history):
        return True
    if baseline is True:
        return True
    if baseline is False and not (coverage or {}).get('gaps'):
        return baseline
    return False if complete_history(history, coverage or {}) else UNKNOWN
