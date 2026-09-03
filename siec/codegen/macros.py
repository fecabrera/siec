"""Expansion of '@macro' declarations, substituted at their uses."""

import copy
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass

from siec.ast import (Assign, Block, BlockExpr, Call, Emit, Index,
                      IndexAssign, Member, MemberAssign, Var)
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator
from siec.codegen.resolution import fold_qualified


def _resolve_macro_name(gen: CodeGenerator, name: str) -> str | None:
    """
    Resolve a macro use through the same module/member bindings as other
    declarations. The returned spelling is the registered, unqualified macro
    name; None means the written name does not denote a visible macro.
    """
    if "." in name:
        resolved = gen.resolve_qualified(name.split("."))
    else:
        resolved = gen.member_bindings.get((gen.current_file, name), name)
        if resolved == name and not gen.sees(name):
            return None

    return resolved if resolved in gen.macros else None


def _resolve_macro_call(gen: CodeGenerator, call: Call) -> str | None:
    """Resolve and canonicalize a call's possibly qualified macro name."""
    if (getattr(call, "macro_resolved", False)
            and call.name in gen.macros):
        return call.name

    previous = gen.current_file
    gen.current_file = getattr(call, "macro_argument_file", previous)
    try:
        name = _resolve_macro_name(gen, call.name)
    finally:
        gen.current_file = previous
    if name is not None:
        call.name = name
        call.macro_resolved = True
    return name


def _resolve_macro_var(gen: CodeGenerator, var: Var) -> str | None:
    """Resolve and canonicalize an object-like macro reference."""
    if getattr(var, "macro_resolved", False) and var.name in gen.macros:
        return var.name

    # A qualified member chain is folded to its exported name and carries
    # the module file it came through. That lookup already checked exports.
    if (getattr(var, "module_file", None) is not None
            and var.name in gen.macros):
        var.macro_resolved = True
        return var.name

    previous = gen.current_file
    gen.current_file = getattr(var, "macro_argument_file", previous)
    try:
        name = _resolve_macro_name(gen, var.name)
    finally:
        gen.current_file = previous
    if name is not None:
        var.name = name
        var.macro_resolved = True
    return name


def _object_macro_call(var: Var, name: str) -> Call:
    """Build the resolved zero-value-argument call for an object macro."""
    if (cached := getattr(var, "macro_call", None)) is not None:
        return cached

    call = Call(name, [], var.type_args)
    call.macro_resolved = True
    for attr in ("macro_argument_file", "macro_type_args_resolved"):
        if hasattr(var, attr):
            setattr(call, attr, getattr(var, attr))
    var.macro_call = call
    return call


@dataclass(frozen=True)
class MacroUse:
    """One canonical macro invocation shared by every compiler phase."""
    name: str
    call: Call


def resolve_macro_use(gen: CodeGenerator, expr, scope: dict) -> MacroUse | None:
    """
    Resolve every macro spelling in one place: qualified or plain, imported
    or local, function-like calls and object-like references. Lexical values
    shadow declarations before lookup, and substituted arguments retain the
    source view where they were written.
    """
    if isinstance(expr, Call):
        if expr.name in scope:
            return None
        if not hasattr(expr, "macro_argument_file"):
            expr.macro_argument_file = gen.current_file
        name = _resolve_macro_call(gen, expr)
        return MacroUse(name, expr) if name is not None else None

    # A qualified object-like use is initially a member chain. Fold it here
    # as part of declaration resolution so lvalue paths and value paths see
    # exactly the same macro use.
    if isinstance(expr, Member):
        var = getattr(expr, "qualified_value", None)
        if var is None:
            var = fold_qualified(gen, expr, scope)
        if var is None:
            return None
        return resolve_macro_use(gen, var, scope)

    if not isinstance(expr, Var) or expr.name in scope:
        return None

    name = _resolve_macro_var(gen, expr)
    if name is None or gen.macros[name].params is not None:
        return None

    return MacroUse(name, _object_macro_call(expr, name))


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
            from siec.codegen.resolution import expression_view

            with expression_view(gen, call):
                type_args = [expand_alias(gen, arg)
                             for arg in call.type_args]
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

        for arg in call.args:
            mark_macro_argument_view(arg, gen.current_file)
        mapping = dict(zip(macro.params, call.args))

    if macro.body is None:
        call.expansion = copy.deepcopy(macro.value)
        prepare_macro_template(gen, call.expansion, macro)
        if type_mapping:
            from siec.codegen.generics import substitute_types

            substitute_types(call.expansion, type_mapping)
        call.expansion = substitute(call.expansion, mapping)
        from siec.codegen.ownership import inherit_expression_identity

        inherit_expression_identity(call, call.expansion)
        return call.expansion

    body = copy.deepcopy(macro.body)
    prepare_macro_template(gen, body, macro)
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
    use = resolve_macro_use(gen, expr, scope)
    if use is not None:
        return use.name, macro_expansion(gen, use.call)

    return None


def emit_macro_assignment(gen: CodeGenerator, builder, name: str, target,
                          value, line: int, scope: dict, checked_stmt) -> None:
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

    if hasattr(checked_stmt, "assignment_action"):
        assignment.initialization = checked_stmt.initialization
        assignment.assignment_action = checked_stmt.assignment_action

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


def prepare_macro_template(gen: CodeGenerator, node, macro) -> None:
    """
    Resolve nested macro uses and canonicalize explicit type arguments written
    in a macro definition before value arguments are spliced into it.

    A nested generic macro changes the active declaration view. Its type
    arguments nevertheless belong to the surrounding macro's source: in
    ``OUTER = INNER<Private>()``, ``Private`` resolves beside ``OUTER``.
    Canonicalizing here preserves that ownership across nested expansion.
    Doing it before value substitution also leaves type arguments inside a
    caller expression under the caller's own view.
    """
    if isinstance(node, list):
        for item in node:
            prepare_macro_template(gen, item, macro)
        return

    if not is_dataclass(node):
        return

    with macro_view(gen, macro.name):
        if isinstance(node, Call):
            resolve_macro_use(gen, node, {})
        elif isinstance(node, Var):
            resolve_macro_use(gen, node, {})

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
            prepare_macro_template(gen, getattr(node, field.name), macro)


def mark_macro_argument_view(node, file: str) -> None:
    """
    Retain where a value argument was written when substitution carries it
    through one or more macro declaration views. Resolution still happens at
    use time, after lexical locals have had their normal chance to shadow it.
    """
    if isinstance(node, list):
        for item in node:
            mark_macro_argument_view(item, file)
        return

    if not is_dataclass(node):
        return

    if not hasattr(node, "macro_argument_file"):
        node.macro_argument_file = file

    for field in fields(node):
        mark_macro_argument_view(getattr(node, field.name), file)


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
