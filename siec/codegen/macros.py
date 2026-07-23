"""Expansion of '@macro' declarations, substituted at their uses."""

import copy
from contextlib import contextmanager
from dataclasses import fields, is_dataclass

from siec.ast import (Assign, Block, BlockExpr, Call, Emit, Index,
                      IndexAssign, Member, MemberAssign, Var)
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator


@contextmanager
def macro_view(gen: CodeGenerator, name: str):
    """
    Resolve names under the macro's defining file's view: an expansion's
    own names live where the macro was written, not where it is used.
    """
    previous = gen.current_file
    gen.current_file = gen.macros[name].file
    try:
        yield
    finally:
        gen.current_file = previous


def macro_expansion(gen: CodeGenerator, call: Call):
    """
    A macro use's substitution: the macro's expression with the
    arguments in place of its parameters, or its block - a 'BlockExpr'
    when an 'emit' inside can produce the use's value, a plain 'Block'
    otherwise. A bare object-like use arrives as a zero-argument call.

    The expansion is cached on the call node, so inference and emission
    see the same nodes.
    """
    if (cached := getattr(call, "expansion", None)) is not None:
        return cached

    macro = gen.macros[call.name]
    if call.type_args:
        raise TypeError(f"macro {call.name!r} takes no type arguments")

    if macro.params is None:
        if call.args:
            raise TypeError(f"macro {call.name!r} takes no parameters")

        mapping = {}
    else:
        if len(call.args) != len(macro.params):
            raise TypeError(f"macro {call.name!r} takes {len(macro.params)} "
                            f"argument(s), got {len(call.args)}")

        mapping = dict(zip(macro.params, call.args))

    if macro.body is None:
        call.expansion = substitute(copy.deepcopy(macro.value), mapping)
        return call.expansion

    body = [substitute(copy.deepcopy(stmt), mapping) for stmt in macro.body]

    call.expansion = (BlockExpr(body) if first_emit(body) is not None
                      else Block(body, line=macro.line))
    return call.expansion


def macro_place(gen: CodeGenerator, expr, scope: dict):
    """
    The (name, expansion) a macro use in lvalue position stands for: an
    object-like macro's bare name, or a function-like one's call. None
    when the expression is no macro use; a scope variable shadows one.
    """
    if (isinstance(expr, Var) and expr.name not in scope
            and expr.name in gen.macros
            and gen.macros[expr.name].params is None):
        return expr.name, macro_expansion(gen, Call(expr.name, []))

    if isinstance(expr, Call) and expr.name in gen.macros:
        return expr.name, macro_expansion(gen, expr)

    return None


def emit_macro_assignment(gen: CodeGenerator, builder, name: str, target,
                          value, line: int, scope: dict) -> None:
    """
    Assign through a macro's expansion: the expanded target rebuilds
    into the assignment it means, emitted in the macro's view.
    """
    # deferred imports: statements and macros are mutually recursive, and
    # the parser owns the lvalue-to-assignment mapping
    from siec.codegen.statements import emit_statement_body
    from siec.parser.statements import make_assignment

    try:
        assignment = make_assignment(target, value, line)
    except SyntaxError:
        raise TypeError(f"macro {name!r} does not expand to an "
                        "assignable place") from None

    with macro_view(gen, name):
        emit_statement_body(gen, builder, assignment, scope)


def substitute(node, mapping: dict):
    """
    Replace each parameter's appearance in a copied macro body with its
    argument expression, C-macro-style: an argument named twice runs twice.
    """
    if isinstance(node, Var) and not node.qualified and node.name in mapping:
        return copy.deepcopy(mapping[node.name])

    # 'param = v;' assigns through the argument, which must be a place
    if isinstance(node, Assign) and not node.qualified and node.name in mapping:
        target = mapping[node.name]
        value = substitute(node.value, mapping)

        if isinstance(target, Var):
            return Assign(target.name, value, target.qualified, line=node.line)

        if isinstance(target, Member):
            return MemberAssign(copy.deepcopy(target.base), target.field,
                                value, line=node.line)

        if isinstance(target, Index):
            return IndexAssign(copy.deepcopy(target.base),
                               copy.deepcopy(target.index), value, line=node.line)

        raise TypeError(f"the macro assigns to its parameter {node.name!r}, "
                        "so the argument must be assignable")

    if isinstance(node, list):
        return [substitute(item, mapping) for item in node]

    if is_dataclass(node):
        for field in fields(node):
            setattr(node, field.name, substitute(getattr(node, field.name), mapping))

    return node


def first_emit(node) -> Emit | None:
    """
    The first 'emit' a macro body reaches, deciding whether an expansion
    produces a value; one inside a nested block expression belongs to it
    and does not count.
    """
    if isinstance(node, Emit):
        return node

    if isinstance(node, BlockExpr):
        return None

    if isinstance(node, list):
        for item in node:
            if (found := first_emit(item)) is not None:
                return found

        return None

    if is_dataclass(node):
        for field in fields(node):
            if (found := first_emit(getattr(node, field.name))) is not None:
                return found

    return None


def check_macro_cycles(gen: CodeGenerator) -> None:
    """
    Reject a macro that expands into itself, straight or roundabout.
    """
    def calls_in(node, found: set) -> set:
        # a call reaches a macro; so does an object-like one's bare name
        if isinstance(node, Call) and node.name in gen.macros:
            found.add(node.name)

        if (isinstance(node, Var) and node.name in gen.macros
                and gen.macros[node.name].params is None):
            found.add(node.name)

        if isinstance(node, list):
            for item in node:
                calls_in(item, found)
        elif is_dataclass(node):
            for field in fields(node):
                calls_in(getattr(node, field.name), found)

        return found

    graph = {name: calls_in(macro.body if macro.body is not None
                            else macro.value, set())
             for name, macro in gen.macros.items()}

    def visit(name: str, chain: list) -> None:
        for callee in graph[name]:
            if callee in chain:
                cycle = " -> ".join([*chain[chain.index(callee):], callee])
                raise TypeError(f"macro cycle: {cycle}")

            visit(callee, [*chain, callee])

    for name, macro in gen.macros.items():
        with source_location(line=macro.line, file=macro.file):
            visit(name, [name])
