"""Known user-input failures that are safe to render without a traceback."""


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
