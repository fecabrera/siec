"""Shared source-aware resolution of expression names."""

from contextlib import contextmanager

from siec.ast import Member, Var
from siec.codegen.generator import CodeGenerator


@contextmanager
def expression_view(gen: CodeGenerator, expr):
    """Resolve an expression under the source view where it was written."""
    previous = gen.current_file
    gen.current_file = getattr(expr, "macro_argument_file", previous)
    try:
        yield
    finally:
        gen.current_file = previous


def fold_qualified(gen: CodeGenerator, expr, scope: dict):
    """
    Fold a pure ``a.b.name`` chain into the value its module binding exports.

    Macro substitution can move the chain beneath the macro declaration's
    source view. In that case resolution still belongs to the caller where
    the argument was written. A scoped root shadows a module binding.
    """
    names, node = [], expr
    while isinstance(node, Member):
        names.append(node.field)
        node = node.base

    if not isinstance(node, Var) or node.name in scope:
        return None

    names.append(node.name)
    names.reverse()

    with expression_view(gen, expr):
        found = gen.resolve_member(names)
    if found is None:
        return None

    var = Var(found[0], qualified=True)
    var.module_file = found[1]
    return var
