"""Tenhou match-end facts, not a ranking strategy or a next-hand scheduler."""


def match_status(scores, *, dealer, wind, number, repeat, kind, match_length='south', bankruptcy=True):
    initial_dealer = (dealer-number+1) % 4
    ranking = sorted(range(4), key=lambda s: (-scores[s], (s-initial_dealer) % 4))
    current = ('east','south','west','north').index(wind)*4 + number-1
    last = 7 if match_length == 'south' else 3
    reason = None
    if bankruptcy and min(scores) < 0:
        reason = 'bankruptcy'
    elif kind in ('win','exhaustive') and current >= last:
        if repeat:
            if ranking[0] == dealer and scores[dealer] >= 30000:
                reason = 'dealer_top_stop'
        elif max(scores) >= 30000:
            reason = 'target_reached'
        elif current >= last+4:
            reason = 'extension_limit'
    return dict(ended=reason is not None, reason=reason, ranking=ranking)
