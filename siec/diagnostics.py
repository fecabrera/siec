"""Known user-input failures and structured compile diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Severity = Literal["error", "warning", "note"]


class DiagnosticError(Exception):
    """Base class for an expected, user-facing compiler diagnostic."""


class ParseLimitError(DiagnosticError, SyntaxError):
    """Source syntax exceeded a parser resource boundary."""


class ConstantEvaluationError(DiagnosticError, TypeError):
    """A constant expression requested an invalid integer operation."""


class PackageInputError(DiagnosticError, ValueError):
    """A package manifest value is unsafe or malformed."""


class InputFormatError(DiagnosticError, OSError):
    """A user-supplied native input has an invalid container format."""


@dataclass(frozen=True)
class Diagnostic:
    """
    One structured compile diagnostic.

    Warnings accumulate on the generator during compilation; the CLI and
    LSP format or publish them without reading process stderr. Errors may
    still raise today; this type is the shared rendering model both sides
    should converge on.
    """

    severity: Severity
    message: str
    file: str | None = None
    line: int | None = None
    code: str | None = None
