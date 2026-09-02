"""Source-level callable arity metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CallArity:
    """Describe the source arguments that one callable accepts."""

    minimum: int
    maximum: int | None
    parameter_count: int
    variadic: bool = False

    @classmethod
    def exact(cls, count: int) -> "CallArity":
        """Build arity for a callable that requires an exact count."""
        return cls(count, count, count)

    @classmethod
    def from_parameters(cls, parameters: Iterable, *, variadic: bool = False,
                        var_arg: bool = False) -> "CallArity":
        """Build arity from parameters with optional trailing defaults."""
        params = list(parameters)
        minimum = len(params)
        while (minimum
               and getattr(params[minimum - 1], "default", None) is not None):
            minimum -= 1

        if variadic:
            minimum = min(minimum, max(0, len(params) - 1))

        maximum = None if variadic or var_arg else len(params)
        return cls(minimum, maximum, len(params), variadic)

    def accepts(self, count: int) -> bool:
        """Return whether ``count`` source arguments are valid."""
        return (count >= self.minimum
                and (self.maximum is None or count <= self.maximum))

    def error(self, count: int) -> str | None:
        """Return the argument-count error kind, if one exists."""
        if count < self.minimum:
            return "too few"
        if self.maximum is not None and count > self.maximum:
            return "too many"
        return None

    def without_prefix(self, count: int) -> "CallArity":
        """Remove implicit leading parameters from the source call shape."""
        maximum = (None if self.maximum is None
                   else max(0, self.maximum - count))
        return CallArity(
            max(0, self.minimum - count),
            maximum,
            max(0, self.parameter_count - count),
            self.variadic,
        )
