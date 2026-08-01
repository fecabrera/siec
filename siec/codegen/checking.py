"""Backend-neutral checking of resolved function bodies."""

from dataclasses import fields, is_dataclass

from siec.ast import (
    AggregateLiteral,
    ArrayLiteral,
    Assign,
    BinaryOp,
    Block,
    BlockExpr,
    Break,
    Call,
    Case,
    Cast,
    CharLiteral,
    CompoundAssign,
    Continue,
    Defer,
    Emit,
    Expr,
    ExprStmt,
    For,
    Foreach,
    Function,
    If,
    Index,
    IndexAssign,
    IntLiteral,
    Let,
    LetTuple,
    Member,
    MemberAssign,
    MethodCall,
    Move,
    NullLiteral,
    RefAssign,
    Return,
    SizeOf,
    Slice,
    Ternary,
    Try,
    TupleLiteral,
    TypeId,
    TypeName,
    TypeOf,
    UnaryOp,
    Var,
    While,
)
from siec.codegen.aliases import expand_alias
from siec.codegen.errors import error_call_trace, source_location
from siec.codegen.generator import CodeGenerator, Variable
from siec.codegen.inference import (
    expr_sie_type,
    infer_type,
    member_field,
    try_arms,
    untyped_reason,
)
from siec.codegen.types import (
    fn_type_parts,
    is_aliasing,
    is_const,
    is_reference,
    sized_array,
    strip_const,
    strip_reference,
    validate_type,
)

NO_EMIT = object()


def checked_variable(type_name: str, *, moved: bool = False) -> Variable:
    """A scope entry carrying only its semantic type."""
    return Variable(None, type_name, moved=moved)


def check_function(gen: CodeGenerator, fn: Function) -> None:
    """Check one resolved function body without constructing LLVM IR."""
    with source_location(line=fn.line, file=fn.file), error_call_trace(gen):
        gen.current_file = fn.file
        symbol = resolved_symbol(gen, fn)
        gen.current_function = symbol
        gen.checking_function = fn
        gen.current_line = fn.line

        if fn.asm is not None:
            gen.checked_functions.add(symbol)
            return

        scope = {
            param.name: checked_variable(param.type)
            for param in fn.params
        }
        params = dict(scope)
        terminates = check_block(gen, fn.body or [], scope, fn)

        from siec.codegen.results import check_results

        check_results(gen, fn, params)

        if (fn.return_type is not None and not fn.noreturn
                and not terminates):
            raise TypeError(f"function {fn.name!r} must return a value")

        gen.checked_functions.add(symbol)


def resolved_symbol(gen: CodeGenerator, fn: Function) -> str:
    """The resolved overload symbol belonging to a function body."""
    from siec.codegen.overloads import overload_symbol

    return overload_symbol(gen, gen.resolve_symbol(fn.name), fn.params)


def check_block(gen: CodeGenerator, statements: list, scope: dict,
                fn: Function, *, loop: bool = False,
                emit_type: str | None | object = NO_EMIT) -> bool:
    """Check a statement list and report whether every path terminates."""
    terminates = False
    for stmt in statements:
        if terminates:
            break
        terminates = check_statement(
            gen,
            stmt,
            scope,
            fn,
            loop=loop,
            emit_type=emit_type,
        )
    return terminates


def merge_moved(parent: dict, paths: list[dict]) -> None:
    """Conservatively merge ownership states from continuing CFG paths."""
    for name, variable in list(parent.items()):
        states = [path[name].moved for path in paths if name in path]
        if states:
            parent[name] = checked_variable(
                variable.type, moved=any(states))


def check_statement(gen: CodeGenerator, stmt, scope: dict, fn: Function, *,
                    loop: bool = False,
                    emit_type: str | None | object = NO_EMIT) -> bool:
    """Check one statement, returning whether it terminates its path."""
    with source_location(line=getattr(stmt, "line", 0)):
        if line := getattr(stmt, "line", 0):
            gen.current_line = line

        if isinstance(stmt, Let):
            inferred = stmt.type is None
            type_name = expand_alias(gen, stmt.type)
            if type_name is None:
                type_name = infer_type(gen, stmt.value, scope)
                if type_name is None:
                    if (reason := untyped_reason(
                            gen, stmt.value, scope)) is not None:
                        raise reason
                    raise TypeError(f"cannot infer a type for {stmt.name!r}: "
                                    "annotate it explicitly")

            stmt.type = type_name
            stmt.expanded = True
            if is_reference(type_name):
                raise TypeError("a reference cannot type a variable")
            validate_type(type_name, gen.structs)
            if (sized := sized_array(type_name)) is not None:
                from siec.codegen.enums import evaluate_size

                evaluate_size(gen, sized[1])
                type_name = sized[0]

            if stmt.value is not None:
                context = (
                    None
                    if inferred and isinstance(stmt.value, Ternary)
                    else type_name
                )
                checked = check_expression(
                    gen, stmt.value, scope, context)
                if inferred and checked is not None:
                    type_name = checked
                    stmt.type = checked
            else:
                check_type_defaults(gen, type_name)
            scope[stmt.name] = checked_variable(type_name)
            return False

        if isinstance(stmt, LetTuple):
            value_type = check_expression(gen, stmt.value, scope)
            bind_tuple_pattern(gen, stmt.pattern, value_type, scope)
            return False

        if isinstance(stmt, (Assign, MemberAssign, RefAssign, IndexAssign)):
            target = assignment_target(stmt)
            target_type = mutable_lvalue_type(gen, target, scope)
            from siec.codegen.assignment import assignment_action

            action = assignment_action(
                gen, target, target_type, stmt.value, scope)
            if action.call is not None:
                check_expression(gen, action.call, scope)
            else:
                check_expression(gen, action.value, scope, target_type)

            if isinstance(target, Var) and target.name in scope:
                variable = scope[target.name]
                scope[target.name] = checked_variable(
                    variable.type, moved=False)
            return False

        if isinstance(stmt, CompoundAssign):
            target_type = mutable_lvalue_type(gen, stmt.target, scope)
            from siec.codegen.methods import resolve_method
            from siec.codegen.statements import COMPOUND_METHODS

            method = COMPOUND_METHODS.get(stmt.op)
            concrete = strip_const(target_type)
            if (method is not None
                    and (concrete in gen.structs
                         or concrete.endswith("[]"))
                    and resolve_method(gen, concrete, method) is not None):
                check_expression(
                    gen,
                    MethodCall(stmt.target, method, [stmt.value]),
                    scope,
                )
                return False

            replacement = BinaryOp(stmt.op, stmt.target, stmt.value)
            from siec.codegen.assignment import assignment_action

            action = assignment_action(
                gen, stmt.target, target_type, replacement, scope)
            if action.call is not None:
                check_expression(gen, action.call, scope)
            else:
                check_expression(gen, action.value, scope, target_type)
            return False

        if isinstance(stmt, Return):
            if fn.noreturn:
                raise TypeError(f"'@noreturn' function {fn.name!r} "
                                "cannot return")
            if stmt.value is None:
                return True
            return_type = (
                "i32"
                if fn.name == "main" and fn.return_type is None
                else fn.return_type
            )
            if return_type is None:
                raise TypeError(f"function {fn.name!r} has no return type "
                                "and cannot return a value")
            check_expression(
                gen,
                stmt.value,
                scope,
                strip_reference(return_type),
            )
            return True

        if isinstance(stmt, ExprStmt):
            # a statement calling an '@noreturn' function ends its path:
            # the resolved callee decides, so a generic 'panic' instance
            # or a picked overload terminates like a concrete call
            gen.checked_call = None
            check_expression(gen, stmt.expr, scope)
            return (isinstance(stmt.expr, (Call, MethodCall))
                    and gen.checked_call in gen.noreturns)

        if isinstance(stmt, Block):
            inner = dict(scope)
            terminates = check_block(
                gen,
                stmt.body,
                inner,
                fn,
                loop=loop,
                emit_type=emit_type,
            )
            if not terminates:
                merge_moved(scope, [inner])
            return terminates

        if isinstance(stmt, If):
            check_expression(gen, stmt.condition, scope)
            left_scope = dict(scope)
            left = check_block(gen, stmt.body, left_scope, fn,
                               loop=loop, emit_type=emit_type)
            right_scope = dict(scope)
            right = False
            if stmt.orelse is not None:
                right = check_block(
                    gen, stmt.orelse, right_scope, fn,
                    loop=loop, emit_type=emit_type)

            continuing = []
            if not left:
                continuing.append(left_scope)
            if not right:
                continuing.append(right_scope)
            merge_moved(scope, continuing)
            return bool(left and right)

        if isinstance(stmt, Case):
            if isinstance(stmt.subject, TypeOf):
                from siec.codegen.expressions import type_operand
                from siec.codegen.statements import expand_when_interface

                stmt.arms = [
                    expanded
                    for arm in stmt.arms
                    for expanded in expand_when_interface(gen, arm, scope)
                ]
                for arm in stmt.arms:
                    arm.values = [
                        type_operand(gen, value, scope)
                        for value in arm.values
                    ]

            subject = check_expression(gen, stmt.subject, scope)
            from siec.codegen.inference import numeric_class

            arms = []
            continuing = []
            for arm in stmt.arms:
                for value in arm.values:
                    # a char arm against an integer subject constant-emits
                    # at the subject's own width, like any arm value
                    arm_expected = (
                        None
                        if isinstance(value, CharLiteral)
                        and numeric_class(subject) is not None
                        else subject
                    )
                    check_expression(gen, value, scope, arm_expected)
                arm_scope = dict(scope)
                terminates = check_block(
                    gen, arm.body, arm_scope, fn,
                    loop=loop, emit_type=emit_type)
                arms.append(terminates)
                if not terminates:
                    continuing.append(arm_scope)
            other_scope = dict(scope)
            other = False
            if stmt.orelse is not None:
                other = check_block(
                    gen, stmt.orelse, other_scope, fn,
                    loop=loop, emit_type=emit_type)
            if not other:
                continuing.append(other_scope)
            merge_moved(scope, continuing)
            return bool(arms and all(arms) and other)

        if isinstance(stmt, While):
            check_expression(gen, stmt.condition, scope)
            inner = dict(scope)
            gen.checking_loop_depth += 1
            try:
                check_block(gen, stmt.body, inner, fn, loop=True,
                            emit_type=emit_type)
            finally:
                gen.checking_loop_depth -= 1
            merge_moved(scope, [scope, inner])
            return False

        if isinstance(stmt, For):
            inner = dict(scope)
            check_statement(gen, stmt.init, inner, fn, loop=True)
            check_expression(gen, stmt.condition, inner)
            body_scope = dict(inner)
            gen.checking_loop_depth += 1
            try:
                check_block(gen, stmt.body, body_scope, fn, loop=True,
                            emit_type=emit_type)
                check_statement(gen, stmt.step, body_scope, fn, loop=True)
            finally:
                gen.checking_loop_depth -= 1
            merge_moved(inner, [inner, body_scope])
            merge_moved(scope, [inner])
            return False

        if isinstance(stmt, Foreach):
            check_foreach(gen, stmt, scope, fn, emit_type)
            return False

        if isinstance(stmt, Defer):
            inner = stmt.stmt.body if isinstance(stmt.stmt, Block) else []
            if any(isinstance(nested, Defer) for nested in inner):
                raise TypeError("a defer cannot hold another defer directly; "
                                "give it a scope of its own")
            check_statement(gen, stmt.stmt, dict(scope), fn, loop=loop,
                            emit_type=emit_type)
            return False

        if isinstance(stmt, Emit):
            if emit_type is NO_EMIT:
                raise TypeError("'emit' outside a block expression")
            if emit_type is None:
                raise TypeError("nothing here takes a value: the result this "
                                "'try' unwraps carries only an error")
            check_expression(gen, stmt.value, scope, emit_type)
            return True

        if isinstance(stmt, (Break, Continue)):
            if not loop:
                word = "break" if isinstance(stmt, Break) else "continue"
                raise TypeError(f"'{word}' outside a loop")
            return True

        raise TypeError(f"cannot check statement {stmt!r}")


def assignment_target(stmt) -> Expr:
    """The expression place named by any assignment statement."""
    if isinstance(stmt, Assign):
        return Var(stmt.name, qualified=stmt.qualified)
    if isinstance(stmt, MemberAssign):
        return Member(stmt.base, stmt.field)
    if isinstance(stmt, RefAssign):
        return stmt.target
    return Index(stmt.base, stmt.index)


def lvalue_type(gen: CodeGenerator, expr: Expr, scope: dict) -> str:
    """Resolve an assignable expression's Sie type."""
    from siec.codegen.macros import macro_place, macro_view

    if (place := macro_place(gen, expr, scope)) is not None:
        name, expansion = place
        with macro_view(gen, name):
            try:
                return lvalue_type(gen, expansion, scope)
            except (NameError, TypeError):
                raise TypeError(
                    f"macro {name!r} does not expand to an "
                    "assignable place") from None

    if isinstance(expr, Var):
        if expr.name in scope:
            return strip_reference(scope[expr.name].type)
        symbol = gen.resolve_symbol(expr.name)
        if symbol in gen.globals:
            return gen.globals[symbol]
        raise NameError(f"undefined variable {expr.name!r}")

    if isinstance(expr, Member):
        return member_field(gen, expr, scope)[1]

    if not (isinstance(expr, (Index, Call, MethodCall))
            or isinstance(expr, UnaryOp) and expr.op == "*"):
        raise TypeError("expression is not assignable")

    type_name = expr_sie_type(gen, expr, scope)
    if type_name is None:
        if (reason := untyped_reason(gen, expr, scope)) is not None:
            raise reason
        raise TypeError("expression is not assignable")
    return type_name


def mutable_lvalue_type(gen: CodeGenerator, expr: Expr, scope: dict) -> str:
    """Resolve an lvalue and reject each form of const mutation precisely."""
    from siec.codegen.lvalues import reject_const_base
    from siec.codegen.macros import macro_place, macro_view

    if (place := macro_place(gen, expr, scope)) is not None:
        name, expansion = place
        with macro_view(gen, name):
            try:
                lvalue_type(gen, expansion, scope)
            except (NameError, TypeError):
                raise TypeError(
                    f"macro {name!r} does not expand to an "
                    "assignable place") from None
            return mutable_lvalue_type(gen, expansion, scope)

    if isinstance(expr, Var):
        if expr.name in scope:
            declared = scope[expr.name].type
            if is_const(declared):
                raise TypeError(
                    f"cannot assign to const variable {expr.name!r}")
            return strip_reference(declared)

        symbol = gen.resolve_symbol(expr.name)
        if symbol in gen.globals:
            declared = gen.globals[symbol]
            if is_const(declared):
                raise TypeError(
                    f"cannot assign to const variable {expr.name!r}")
            return strip_reference(declared)
        if expr.name in gen.constants:
            raise TypeError(f"cannot reassign constant {expr.name!r}")
        raise NameError(f"undefined variable {expr.name!r}")

    if isinstance(expr, Member):
        declared = member_field(gen, expr, scope)[1]
        if is_const(declared):
            raise TypeError(f"cannot assign to const field {expr.field!r}")
        reject_const_base(gen, scope, expr.base)
        return declared

    if isinstance(expr, Index):
        reject_const_base(gen, scope, expr.base)
    elif isinstance(expr, UnaryOp) and expr.op == "*":
        reject_const_base(gen, scope, expr.operand)
    elif isinstance(expr, Cast):
        declared = expr_sie_type(gen, expr, scope)
        if is_const(declared):
            raise TypeError("cannot assign through a const cast")
        reject_const_base(gen, scope, expr.operand)
    elif isinstance(expr, (Call, MethodCall)):
        declared = expr_sie_type(gen, expr, scope)
        if is_const(declared):
            raise TypeError(
                f"cannot assign through a {declared!r} reference")

    return lvalue_type(gen, expr, scope)


def bind_tuple_pattern(gen: CodeGenerator, pattern: list,
                       type_name: str | None, scope: dict) -> None:
    """Bind a nested tuple pattern from its resolved generic arguments."""
    from siec.codegen.generics import split_generic

    parts = split_generic(strip_const(type_name or ""))
    if parts is None or parts[0] != "Tuple":
        raise TypeError(f"cannot destructure a {type_name or '?'!r} value: "
                        "it is not a tuple")
    args = parts[1]
    if len(pattern) != len(args):
        take = len(args)
        raise TypeError(
            f"the pattern binds {len(pattern)} "
            f"name{'s' if len(pattern) != 1 else ''}; "
            f"{strip_const(type_name)!r} has {take} "
            f"element{'s' if take != 1 else ''}")

    for name, arg in zip(pattern, args):
        if isinstance(name, list):
            bind_tuple_pattern(gen, name, arg, scope)
        else:
            scope[name] = checked_variable(arg)


def check_foreach(gen: CodeGenerator, stmt: Foreach, scope: dict,
                  fn: Function, emit_type: str | None) -> None:
    """Resolve iterator methods and check a foreach body semantically."""
    from siec.codegen.methods import iteration_getter, resolve_method
    from siec.codegen.overloads import overload_candidates

    source_type = check_expression(gen, stmt.iterable, scope)
    source = strip_reference(source_type) if source_type else None
    if not source:
        raise TypeError("cannot iterate: the expression has no type")

    if (getter := iteration_getter(gen, source)) is not None:
        call = MethodCall(stmt.iterable, getter, [])
        it_type = check_expression(gen, call, scope)
    elif resolve_method(gen, source, "has_next") is not None:
        it_type = strip_const(source)
    else:
        raise TypeError(f"cannot iterate a {source_type!r} value: it is "
                        "neither an Iterable nor an Iterator")

    has_next = resolve_method(gen, it_type, "has_next")
    next_ = resolve_method(gen, it_type, "next")
    if has_next is None or next_ is None:
        raise TypeError(f"cannot iterate: type {it_type!r} has no "
                        f"{'has_next' if has_next is None else 'next'!r} "
                        "method")

    next_ret = gen.return_types.get(overload_candidates(gen, next_)[0])
    if not is_reference(next_ret):
        raise TypeError(f"'foreach' needs {it_type!r}'s 'next' to return "
                        "a reference '&T'")

    inner = dict(scope)
    inner[stmt.name] = checked_variable(next_ret)
    gen.checking_loop_depth += 1
    try:
        check_block(gen, stmt.body, inner, fn, loop=True,
                    emit_type=emit_type)
    finally:
        gen.checking_loop_depth -= 1
    merge_moved(scope, [scope, inner])


def check_expression(gen: CodeGenerator, expr: Expr | None, scope: dict,
                     expected: str | None = None) -> str | None:
    """Check an expression and return its inferred Sie type."""
    if expr is None:
        return None

    if isinstance(expr, Var):
        from siec.codegen.constants import constant_view, find_constant
        from siec.codegen.generics import (
            instantiate_function,
            reference_for_target,
            reference_template,
            reference_type,
        )

        if expr.name in scope and scope[expr.name].moved:
            raise TypeError(f"use of moved value {expr.name!r}")

        if expr.type_args is not None:
            template = reference_template(gen, expr.name)
            if template is not None:
                instantiate_function(gen, template, expr.type_args)
                return reference_type(gen, expr)

        if (expected is not None
                and strip_const(expected).startswith("fn(")
                and reference_for_target(gen, expr, expected) is not None):
            return expected

        const = (find_constant(
            gen,
            expr.name,
            getattr(expr, "module_file", None),
        ) if expr.name not in scope else None)
        if const is not None and const.type is None:
            with constant_view(gen, const):
                return check_expression(gen, const.value, scope, expected)

    if isinstance(expr, MethodCall):
        from siec.codegen.methods import resolve_method, takes_receiver

        receiver_type = check_expression(gen, expr.receiver, scope)
        from siec.codegen.deprecation import check_removed_method

        check_removed_method(gen, receiver_type, expr.method)
        symbol = resolve_method(gen, receiver_type, expr.method)
        if symbol is None:
            raise TypeError(
                f"type {receiver_type or '?'} has no method "
                f"{expr.method!r}")
        args = ([expr.receiver, *expr.args] if takes_receiver(gen, symbol)
                else expr.args)
        return check_call(
            gen,
            Call(symbol, args, expr.type_args),
            scope,
            expected,
            resolved=symbol,
        )

    if isinstance(expr, Member):
        from siec.codegen.inference import fold_qualified

        if (folded := fold_qualified(gen, expr, scope)) is not None:
            return check_expression(gen, folded, scope, expected)

    if isinstance(expr, Call):
        return check_call(gen, expr, scope, expected)

    if isinstance(expr, SizeOf):
        from siec.codegen.sizes import size_of

        size_of(gen, expr.name, scope)
        if expected is not None:
            require_fit(gen, expr, "u64", expected)
            return expected
        return "u64"

    if isinstance(expr, (TypeId, TypeName)):
        from siec.codegen.expressions import typename_of

        typename_of(gen, expr.name, scope)
        actual = "u64" if isinstance(expr, TypeId) else "const char[]"
        if expected is not None:
            require_fit(gen, expr, actual, expected)
            return expected
        return actual

    if isinstance(expr, BlockExpr):
        target = expected or block_emit_type(gen, expr.body, scope)
        check_block_expression(gen, expr, scope, target)
        return target

    if isinstance(expr, Try):
        check_expression(gen, expr.result, scope)
        result_type = expr_sie_type(gen, expr.result, scope)
        value_type, error_type = try_arms(gen, expr, scope)

        if value_type is None and expected is not None:
            from siec.codegen.inference import valueless_try

            raise valueless_try(result_type)
        if value_type is not None and expected is not None:
            require_fit(gen, Var(".try.value"), value_type, expected)

        if expr.propagates:
            check_try_propagation(gen, error_type)
            return value_type

        inner = dict(scope)
        if expr.name is not None:
            inner[expr.name] = checked_variable(error_type)

        body = expr.body or []
        if value_type is None and expr.fallback and not expr.braced:
            if body:
                emitted = body[0]
                check_expression(gen, emitted.value, inner)
            return None

        fn = gen.checking_function or Function(
            "<try>",
            [],
            value_type,
            body,
        )
        terminates = check_block(
            gen,
            body,
            inner,
            fn,
            loop=gen.checking_loop_depth > 0,
            emit_type=value_type,
        )
        if value_type is not None and not terminates:
            written = "fallback" if expr.fallback else "'except' arm"
            raise TypeError(
                f"the {written} must leave, or 'emit' a value to stand in: "
                "it has no value of its own to fall out with")
        return value_type

    if isinstance(expr, AggregateLiteral):
        if expected is not None:
            from siec.codegen.inference import type_info

            info = type_info(gen, expected)
            if info is not None and info.fields is not None:
                if info.is_union:
                    raise TypeError(f"union {strip_const(expected)!r} has no "
                                    "aggregate literal: initialize a field")

                if expr.names is None:
                    if len(expr.elements) != len(info.fields):
                        raise TypeError(
                            f"aggregate literal has {len(expr.elements)} "
                            f"elements, expected {len(info.fields)}")
                    pairs = zip(expr.elements, info.fields)
                else:
                    fields_by_name = {
                        field.name: field for field in info.fields
                    }
                    seen = set()
                    pairs = []
                    for name, value in zip(expr.names, expr.elements):
                        if name not in fields_by_name:
                            raise TypeError(
                                f"aggregate literal names unknown field "
                                f"{name!r}")
                        if name in seen:
                            raise TypeError(
                                f"aggregate literal sets field {name!r} "
                                "more than once")
                        seen.add(name)
                        pairs.append((value, fields_by_name[name]))

                for value, field in pairs:
                    field_type = field.type
                    if (is_const(expected)
                            and is_aliasing(field_type)
                            and not is_const(field_type)):
                        field_type = f"const {field_type}"
                    check_expression(gen, value, scope, field_type)
                if expr.names is not None:
                    for field in info.fields:
                        if field.name not in seen:
                            check_field_default(gen, field)
            else:
                for value in expr.elements:
                    check_expression(gen, value, scope)
        return expected

    if isinstance(expr, ArrayLiteral):
        element = None
        if expected is not None and strip_const(expected).endswith("[]"):
            element = strip_const(expected)[:-2]
        for value in expr.elements:
            check_expression(gen, value, scope, element)
        return expected or infer_type(gen, expr, scope)

    if isinstance(expr, TupleLiteral):
        for value in expr.elements:
            check_expression(gen, value, scope)
        return expr_sie_type(gen, expr, scope)

    if isinstance(expr, Ternary):
        check_expression(gen, expr.condition, scope)
        if expected is not None:
            check_expression(gen, expr.then, scope, expected)
            check_expression(gen, expr.orelse, scope, expected)
            return expected

        target = (
            expr_sie_type(gen, expr.then, scope)
            or infer_type(gen, expr.then, scope)
        )
        check_expression(gen, expr.then, scope, target)
        try:
            check_expression(gen, expr.orelse, scope, target)
        except TypeError:
            other = (
                expr_sie_type(gen, expr.orelse, scope)
                or infer_type(gen, expr.orelse, scope)
            )
            raise TypeError(
                f"ternary arms disagree: {target} vs {other}") from None
        return target

    if isinstance(expr, Cast):
        if not getattr(expr, "expanded", False):
            expr.type = expand_alias(gen, expr.type)
            expr.expanded = True
        validate_type(expr.type, gen.structs)
        check_expression(gen, expr.operand, scope)
        return expr.type

    if isinstance(expr, NullLiteral):
        # a bare function reference is a pointer at heart: 'null' clears
        # a callback the same way it clears any pointer target
        target = strip_const(expected) if expected is not None else None
        if (target is not None and not target.endswith("*")
                and not (target.startswith("fn(")
                         and not fn_type_parts(target)[2])):
            raise TypeError("'null' needs a pointer context")
        return expected or "opaque*"

    if isinstance(expr, SizeOf):
        from siec.codegen.sizes import size_of

        size_of(gen, expr.name, scope)
        return "u64"

    if isinstance(expr, (TypeId, TypeName)):
        from siec.codegen.expressions import typename_of

        typename_of(gen, expr.name, scope)
        return "u64" if isinstance(expr, TypeId) else "const char[]"

    if isinstance(expr, Move):
        if not isinstance(expr.operand, Var) or expr.operand.name not in scope:
            raise TypeError("'move' requires an owned local variable")
        if scope[expr.operand.name].moved:
            raise TypeError(f"value {expr.operand.name!r} was already moved")
        moved_type = lvalue_type(gen, expr.operand, scope)
        if is_const(moved_type) or is_reference(moved_type):
            raise TypeError(f"cannot move a {moved_type!r} value")
        scope[expr.operand.name] = checked_variable(moved_type, moved=True)
        if expected is not None:
            require_fit(gen, expr.operand, moved_type, expected)
            return expected
        return moved_type

    if isinstance(expr, Index):
        from siec.codegen.inference import item_call

        if (rewritten := item_call(
                gen, expr, scope, "get_item")) is not None:
            return check_expression(gen, rewritten, scope, expected)

    if isinstance(expr, UnaryOp) and expr.op == "&":
        lvalue_type(gen, expr.operand, scope)
        actual = expr_sie_type(gen, expr, scope)
        if expected is not None:
            require_fit(gen, expr, actual, expected)
            return expected
        return actual

    if (isinstance(expr, BinaryOp) and expr.op in ("==", "!=")
            and (isinstance(expr.left, TypeOf)
                 or isinstance(expr.right, TypeOf))):
        from siec.codegen.expressions import type_operand

        expr.left = type_operand(gen, expr.left, scope)
        expr.right = type_operand(gen, expr.right, scope)

    if isinstance(expr, BinaryOp):
        from siec.codegen.inference import operator_call

        if (rewritten := operator_call(gen, expr, scope)) is not None:
            return check_expression(gen, rewritten, scope, expected)

    # Walk the expression's children first so nested calls request their
    # instances even when the outer node's type is already obvious.
    if is_dataclass(expr):
        for field in fields(expr):
            value = getattr(expr, field.name)
            if is_dataclass(value):
                check_expression(gen, value, scope)
            elif isinstance(value, list):
                for item in value:
                    if is_dataclass(item):
                        check_expression(gen, item, scope)

    actual = expr_sie_type(gen, expr, scope) or infer_type(gen, expr, scope)
    if actual is None:
        if (reason := untyped_reason(gen, expr, scope)) is not None:
            raise reason
        return expected

    if expected is not None:
        require_fit(gen, expr, actual, expected)
        return expected
    return actual


def check_block_expression(gen: CodeGenerator, expr: BlockExpr, scope: dict,
                           target: str | None) -> None:
    """Check an expression block whose emit statements produce target."""
    fn = gen.checking_function or Function("<block>", [], target, expr.body)
    check_block(
        gen,
        expr.body,
        dict(scope),
        fn,
        loop=gen.checking_loop_depth > 0,
        emit_type=target,
    )


def check_try_propagation(gen: CodeGenerator, error_type: str) -> None:
    """Check that a bare ``try`` can return its error from this function."""
    from siec.codegen.inference import result_arms
    from siec.codegen.overloads import display_name

    returned = gen.return_types.get(gen.current_function)
    shown = display_name(gen.current_function or "")
    arms = result_arms(returned)
    if arms is None:
        carried = repr(returned) if returned is not None else "nothing"
        raise TypeError(
            "a bare 'try' hands its error back to the caller, so "
            f"{shown!r} must return a Result; it returns {carried}")

    if strip_const(arms[1]) != strip_const(error_type):
        raise TypeError(
            f"cannot hand a {error_type!r} error back from {shown!r}, "
            f"which returns {returned!r}: a bare 'try' passes the error "
            "on as it is, so both must carry the same one")


def check_type_defaults(gen: CodeGenerator, type_name: str | None) -> None:
    """Check every default recursively used to initialize a struct value."""
    canonical = strip_const(type_name)
    if canonical is None or canonical in gen.checked_default_types:
        return

    info = gen.structs.get(canonical)
    if info is None or info.fields is None or info.is_union:
        return

    # Mark first so a recursive pointer/default graph cannot revisit itself.
    gen.checked_default_types.add(canonical)
    for field in info.fields:
        check_field_default(gen, field)


def check_field_default(gen: CodeGenerator, field) -> None:
    """Check one declared field default, or nested defaults it inherits."""
    if field.default is not None:
        check_expression(gen, field.default, {}, field.type)
    else:
        check_type_defaults(gen, field.type)


def block_emit_type(gen: CodeGenerator, statements: list,
                    scope: dict) -> str | None:
    """The first emit value's type, used when no context supplies one."""
    for stmt in statements:
        if isinstance(stmt, Emit):
            return infer_type(gen, stmt.value, scope)
        nested = stmt.body if isinstance(stmt, Block) else None
        if nested and (found := block_emit_type(gen, nested, scope)):
            return found
    return None


def check_call(gen: CodeGenerator, call: Call, scope: dict,
               expected: str | None = None, *,
               resolved: str | None = None) -> str | None:
    """Resolve and check a call without emitting its instruction."""
    from siec.codegen.generics import instantiate_function, pick_generic_call
    from siec.codegen.methods import constructor_type, method_call, qualified_method
    from siec.codegen.overloads import pick_overload
    from siec.codegen.worklist import activate_function_instance

    if call.name in gen.macros:
        from siec.codegen.macros import macro_expansion, macro_view

        expansion = macro_expansion(gen, call)
        with macro_view(gen, call.name):
            if isinstance(expansion, Block):
                check_block_expression(
                    gen,
                    BlockExpr(expansion.body),
                    scope,
                    expected,
                )
                return expected
            return check_expression(gen, expansion, scope, expected)

    if call.name == "enumerate":
        from siec.codegen.methods import rewrite_enumerate

        if (rewritten := rewrite_enumerate(gen, call, scope)) is not None:
            return check_expression(gen, rewritten, scope, expected)

    indirect = None
    if call.name in scope:
        indirect = scope[call.name].type
    elif "." not in call.name and "::" not in call.name:
        global_symbol = gen.resolve_symbol(call.name)
        if global_symbol in gen.globals:
            indirect = gen.globals[global_symbol]

    if indirect is not None:
        return check_indirect_call(gen, call, scope, indirect)

    symbol = resolved
    receiver = None
    if symbol is None and "::" in call.name:
        symbol = qualified_method(gen, call.name)
    elif symbol is None and "." in call.name:
        symbol = gen.resolve_qualified(call.name.split("."))
        if symbol is None and (found := method_call(gen, call, scope)):
            symbol, receiver = found
        if symbol is not None and receiver is None and symbol in gen.globals:
            return check_indirect_call(gen, call, scope, gen.globals[symbol])
        if symbol is None:
            from siec.codegen.deprecation import check_removed_method

            base, _, method = call.name.rpartition(".")
            if base in scope:
                check_removed_method(
                    gen,
                    expr_sie_type(gen, Var(base), scope),
                    method,
                )
    elif symbol is None:
        if "<" not in call.name and not gen.sees(call.name):
            raise NameError(f"undefined function {call.name!r}")
        symbol = gen.resolve_symbol(call.name)

    if receiver is not None:
        call = Call(call.name, [receiver, *call.args], call.type_args)

    from siec.codegen.deprecation import check_removed

    check_removed(gen, symbol)

    picked = False
    if symbol in gen.overloads:
        try:
            symbol = pick_overload(gen, symbol, call.args, scope)
            picked = True
        except TypeError:
            if gen.generic_functions.get(symbol) is None:
                raise

    activate_function_instance(gen, symbol)
    if not picked and gen.generic_functions.get(symbol) is not None:
        template, type_args = pick_generic_call(
            gen,
            symbol,
            call,
            scope,
            expected,
        )
        symbol = instantiate_function(gen, template, type_args)

    if symbol not in gen.return_types:
        if (ctor := constructor_type(gen, call, symbol)) is not None:
            from siec.codegen.methods import resolve_method, takes_receiver

            validate_type(ctor, gen.structs)
            check_type_defaults(gen, ctor)
            init = resolve_method(gen, ctor, "init")
            if init is None:
                raise TypeError(f"type {ctor!r} has no 'init' method to "
                                "construct it")
            if not takes_receiver(gen, init):
                raise TypeError(
                    f"a static 'init' cannot construct {ctor!r}: the "
                    "constructor passes the instance as its receiver")

            # an overloaded 'init' resolves to the candidate the arguments
            # pick, the instance's type standing in for the receiver they
            # lack; a call no concrete candidate takes falls through to a
            # generic template
            init_picked = False
            if init in gen.overloads:
                from siec.codegen.overloads import pick_overload

                try:
                    init = pick_overload(
                        gen,
                        init,
                        call.args,
                        scope,
                        receiver=ctor,
                    )
                    init_picked = True
                except TypeError:
                    if gen.generic_functions.get(init) is None:
                        raise

            activate_function_instance(gen, init)
            if not init_picked and init in gen.generic_functions:
                inner = dict(scope)
                inner[".ctor"] = checked_variable(f"&{ctor}")
                check_call(
                    gen,
                    Call(init, [Var(".ctor"), *call.args]),
                    inner,
                    resolved=init,
                )
            else:
                params = gen.param_types[init]
                default_offset = 0
                if takes_receiver(gen, init):
                    params = params[1:]
                    default_offset = 1
                check_call_arguments(
                    gen,
                    call,
                    scope,
                    params,
                    init in gen.var_args,
                    init,
                    default_offset=default_offset,
                )
            gen.checked_call = None
            return ctor
        raise NameError(f"undefined function {call.name!r}")

    params = gen.param_types[symbol]
    check_call_arguments(
        gen,
        call,
        scope,
        params,
        symbol in gen.var_args,
        symbol,
    )

    from siec.codegen.deprecation import note_use

    note_use(gen, symbol)
    gen.checked_call = symbol
    return strip_reference(gen.return_types.get(symbol))


def check_indirect_call(gen: CodeGenerator, call: Call, scope: dict,
                        type_name: str) -> str | None:
    """Check a call through a function-typed variable or global."""
    type_name = strip_const(type_name)
    if not type_name.startswith("fn("):
        raise TypeError(
            f"cannot call non-function variable {call.name!r}")
    params, ret, suffix = fn_type_parts(type_name)
    if suffix:
        raise TypeError(
            f"cannot call non-function variable {call.name!r}")
    if len(call.args) != len(params):
        raise TypeError(
            f"function reference {call.name!r} takes {len(params)} "
            f"arguments, got {len(call.args)}")
    check_call_arguments(gen, call, scope, params, False)
    gen.checked_call = None
    return strip_reference(ret)


def check_call_arguments(gen: CodeGenerator, call: Call, scope: dict,
                         params: list[str], var_arg: bool,
                         symbol: str | None = None, *,
                         default_offset: int = 0) -> None:
    """Check call arity and each fixed argument against its parameter."""
    all_defaults, defaults_file = gen.param_defaults.get(
        symbol,
        ([], None),
    )
    defaults = all_defaults[default_offset:]
    required = len(params)
    while (required and required <= len(defaults)
           and defaults[required - 1] is not None):
        required -= 1

    variadic = symbol in gen.variadics
    if variadic:
        required = min(required, len(params) - 1)

    if len(call.args) < required:
        raise TypeError(f"too few arguments to function {call.name!r}")
    if len(call.args) > len(params) and not var_arg and not variadic:
        raise TypeError(f"too many arguments to function {call.name!r}")

    fixed = len(params) - 1 if variadic else len(params)
    for arg, param in zip(call.args[:fixed], params):
        if is_reference(param):
            check_reference_argument(gen, arg, param, scope)
        else:
            check_expression(gen, arg, scope, param)
    for arg in call.args[fixed:]:
        check_expression(gen, arg, scope)

    # Omitted defaults are part of this call's checked expression graph.
    # Resolve them in the declaration's file view and without caller locals,
    # matching the environment emission will later consume.
    previous = gen.current_file
    gen.current_file = defaults_file or previous
    try:
        for index in range(len(call.args), min(fixed, len(defaults))):
            if defaults[index] is not None:
                check_expression(
                    gen,
                    defaults[index],
                    {},
                    params[index],
                )
    finally:
        gen.current_file = previous


def check_reference_argument(gen: CodeGenerator, arg: Expr, param: str,
                             scope: dict) -> None:
    """Check an implicitly borrowed call argument without creating an address."""
    referenced = strip_reference(param)
    declared = expr_sie_type(gen, arg, scope)

    if declared is not None:
        if strip_const(declared) != strip_const(referenced):
            raise TypeError(
                f"cannot bind a {declared!r} value to a {param!r} "
                "parameter")
        if is_const(declared) and not is_const(referenced):
            raise TypeError(
                f"cannot bind a {declared!r} value to a mutable "
                f"{param!r} parameter")

    try:
        lvalue_type(gen, arg, scope)
    except (NameError, TypeError):
        if declared is not None:
            check_expression(gen, arg, scope, referenced)
            return
        if is_const(referenced):
            check_expression(gen, arg, scope, referenced)
            return
        raise TypeError(
            f"a {param!r} parameter needs an assignable argument") from None


def require_fit(gen: CodeGenerator, expr: Expr, actual: str,
                expected: str) -> None:
    """Require an expression to fit a semantic target type."""
    from siec.codegen.overloads import parameter_fit

    if parameter_fit(gen, expr, actual, expected) is not None:
        return

    # a bare integer literal fills a 'char' or 'bool' target like the
    # small integer it emits as; overload ranking still prefers a numeric
    # candidate, so only the direct fit widens here
    if (strip_const(expected) in ("char", "bool")
            and isinstance(expr, IntLiteral)):
        return

    if isinstance(expr, NullLiteral):
        # a bare function reference is a pointer at heart: 'null' clears
        # a callback the same way it clears any pointer target
        target = strip_const(expected)
        if target.startswith("fn(") and not fn_type_parts(target)[2]:
            return
        raise TypeError("'null' needs a pointer context")

    source = strip_const(actual)
    target = strip_const(expected)
    if (is_const(actual) and is_aliasing(source)
            and not is_const(expected)):
        raise TypeError(
            f"cannot use a {actual!r} value where a mutable "
            f"{expected!r} is expected")

    from siec.codegen.inference import enum_backing, numeric_class

    source_class = numeric_class(enum_backing(gen, source))
    target_class = numeric_class(enum_backing(gen, target))
    if source_class is not None and target_class is not None:
        source_prefix, source_width = source_class
        target_prefix, target_width = target_class
        if source_prefix != target_prefix:
            raise TypeError(
                f"cannot implicitly convert {source_prefix}{source_width} "
                f"to {target_prefix}{target_width}: use an explicit cast "
                "between signed, unsigned, and float types")
        if source_width > target_width:
            raise TypeError(
                f"cannot implicitly narrow {source_prefix}{source_width} "
                f"to {target_prefix}{target_width}: use an explicit cast")

    raise TypeError(f"cannot implicitly convert {actual} to {target}")
