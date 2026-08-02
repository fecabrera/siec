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
    own names live where the macro was written, not where it is used. A
    generic expansion has already canonicalized its explicit arguments;
    like a generic function instance, those compiler-carried types are no
    longer gated by the template file's imports.
    """
    previous = gen.current_file
    macro = gen.macros[name]
    gen.current_file = macro.file
    generic = macro.type_params is not None
    if generic:
        gen.ungated_types += 1
    try:
        yield
    finally:
        if generic:
            gen.ungated_types -= 1
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
    if macro.type_params is None:
        if call.type_args is not None:
            raise TypeError(f"macro {call.name!r} takes no type arguments")
        type_mapping = {}
    else:
        if call.type_args is None:
            raise TypeError(f"generic macro {call.name!r} requires explicit "
                            "type arguments")

        expected = len(macro.type_params)
        if len(call.type_args) != expected:
            raise TypeError(
                f"generic macro {call.name!r} takes {expected} type "
                f"argument{'s' if expected != 1 else ''}, "
                f"got {len(call.type_args)}")

        if getattr(call, "macro_type_args_resolved", False):
            type_args = call.type_args
        else:
            from siec.codegen.aliases import expand_alias

            type_args = [expand_alias(gen, arg) for arg in call.type_args]
        for arg in type_args:
            if arg.startswith("const ") or arg.startswith("&"):
                raise TypeError(f"cannot expand macro {call.name!r} with "
                                f"{arg!r}: the argument carries a modifier")

        type_mapping = dict(zip(macro.type_params, type_args))
        if macro.constraints:
            from siec.codegen.interfaces import check_constraints

            check_constraints(gen, macro, type_mapping)

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
        call.expansion = copy.deepcopy(macro.value)
        prepare_macro_type_arguments(gen, call.expansion, macro)
        if type_mapping:
            from siec.codegen.generics import substitute_types

            substitute_types(call.expansion, type_mapping)
        call.expansion = substitute(call.expansion, mapping)
        from siec.codegen.ownership import inherit_expression_identity

        inherit_expression_identity(call, call.expansion)
        return call.expansion

    body = copy.deepcopy(macro.body)
    prepare_macro_type_arguments(gen, body, macro)
    if type_mapping:
        from siec.codegen.generics import substitute_types

        substitute_types(body, type_mapping)
    body = substitute(body, mapping)

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
        return expr.name, macro_expansion(
            gen, Call(expr.name, [], expr.type_args))

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


def prepare_macro_type_arguments(gen: CodeGenerator, node, macro) -> None:
    """
    Canonicalize explicit type arguments written in a macro definition
    before value arguments are spliced into it.

    A nested generic macro changes the active declaration view. Its type
    arguments nevertheless belong to the surrounding macro's source: in
    ``OUTER = INNER<Private>()``, ``Private`` resolves beside ``OUTER``.
    Canonicalizing here preserves that ownership across nested expansion.
    Doing it before value substitution also leaves type arguments inside a
    caller expression under the caller's own view.
    """
    if isinstance(node, list):
        for item in node:
            prepare_macro_type_arguments(gen, item, macro)
        return

    if not is_dataclass(node):
        return

    type_args = getattr(node, "type_args", None)
    if type_args is not None:
        from siec.codegen.aliases import expand_alias

        parameters = frozenset(macro.type_params or ())
        with macro_view(gen, macro.name):
            node.type_args = [
                expand_alias(gen, arg, parameters=parameters)
                for arg in type_args
            ]
        node.macro_type_args_resolved = True

    for field in fields(node):
        if field.name != "type_args":
            prepare_macro_type_arguments(gen, getattr(node, field.name), macro)


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
