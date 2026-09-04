"""Shared operations for generic constraint maps."""


def merge_constraints(target: dict, source: dict | None) -> dict:
    """
    Merge source bounds into target and return target.

    Repeated bounds form a sorted intersection without duplicates. A
    one-bound intersection stays a string instead of a tuple.
    """
    for param, bound in (source or {}).items():
        previous = target.get(param)
        bounds = previous if isinstance(previous, tuple) else (previous,)
        bounds += bound if isinstance(bound, tuple) else (bound,)
        ordered = tuple(sorted(value for value in set(bounds)
                               if value is not None))
        target[param] = ordered[0] if len(ordered) == 1 else ordered
    return target
