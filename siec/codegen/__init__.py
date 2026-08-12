"""LLVM IR generation from a Sie AST.

Subsystems: types (type resolution), functions (declaration and body
emission), statements (statements and control flow), expressions
(literals, variables, calls), generator (shared state and the codegen
entry point).
"""

from siec.codegen.generator import CodeGenerator, codegen
from siec.codegen.hir import TypedExpr, stamp, typed
from siec.codegen.state import (
    EmissionContext,
    FlowContext,
    GenericRegistry,
    SemanticModel,
    SourceContext,
    SymbolTable,
    TypeRegistry,
)
from siec.codegen.types import SCALAR_TYPES, resolve_type

__all__ = [
    "CodeGenerator",
    "codegen",
    "SCALAR_TYPES",
    "resolve_type",
    "SourceContext",
    "SymbolTable",
    "TypeRegistry",
    "GenericRegistry",
    "FlowContext",
    "EmissionContext",
    "SemanticModel",
    "TypedExpr",
    "typed",
    "stamp",
]
