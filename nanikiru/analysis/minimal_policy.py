"""One-step shape ranking; no action permission or score/risk model."""

from ..contracts import Metric


def rank_discard_candidates(candidates) -> Metric:
    """Rank non-forbidden discards by shanten, then unseen ukeire.

    value contains tied tiers of Actions, best tier first. Unknown legality
    remains unknown: these are shape preferences, not executable permissions.
    If any ukeire is unavailable, compare shanten only for the whole set.
    """
    candidates = tuple(c for c in candidates if c.legality != "forbidden")
    if not candidates:
        return Metric("not_applicable", details={"reason": "no_candidates"})
    if any(c.action.kind != "discard" for c in candidates):
        raise ValueError("Minimal policy only ranks discards")
    if len({c.action for c in candidates}) != len(candidates):
        raise ValueError("Duplicate discard candidate")

    def known(candidate, name):
        metric = candidate.metrics.get(name)
        return metric is not None and metric.status == "available" and type(metric.value) is int

    if not all(known(c, "shanten") for c in candidates):
        return Metric("unavailable", details={"missing_fields": ["shanten"]})
    with_ukeire = all(known(c, "ukeire") for c in candidates)
    tiers = {}
    for candidate in candidates:
        key = (candidate.metrics["shanten"].value,
               -candidate.metrics["ukeire"].value if with_ukeire else 0)
        tiers.setdefault(key, []).append(candidate.action)
    return Metric("available" if with_ukeire else "conditional",
                  tuple(tuple(tiers[key]) for key in sorted(tiers)), "heuristic", {
                      "method": "minimal-shape-v1",
                      "criteria": ("shanten", "ukeire") if with_ukeire else ("shanten",),
                      "missing_fields": [] if with_ukeire else ["ukeire"],
                      "win_permission_checked": False,
                      "action_permission_checked": False,
                  })
