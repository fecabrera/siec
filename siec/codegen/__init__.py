"""LLVM IR generation from a Sie AST.

Subsystems: types (type resolution), functions (declaration and body
emission), statements (statements and control flow), expressions
(literals, variables, calls), generator (shared state and the codegen
entry point).
"""

from .generator import CodeGenerator, codegen
from .types import SCALAR_TYPES, resolve_type

__all__ = ["CodeGenerator", "codegen", "SCALAR_TYPES", "resolve_type"]
