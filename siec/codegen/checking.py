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
    ClosureExpr,
    CompoundAssign,
    Continue,
    Defer,
    Drop,
    Emit,
    Expr,
    ExprStmt,
    FloatLiteral,
    For,
    Foreach,
    Function,
    If,
    Index,
    IndexAssign,
    IntLiteral,
    Let,
    LetTuple,
    LocalFunction,
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
from siec.codegen.arity import CallArity
from siec.codegen.errors import error_call_trace, source_location
from siec.codegen.generator import CodeGenerator, Variable
from siec.codegen.inference import (
    check_field_access,
    expr_sie_type,
    infer_type,
    member_field,
    try_arms,
    type_info,
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


def check_member_field(gen: CodeGenerator, expr: Member, scope: dict) -> tuple[int, str]:
    """Resolve a member access and enforce private-field visibility."""
    from siec.codegen.hir import stamp

    index, field_type = member_field(gen, expr, scope)
    base_type = expr_sie_type(gen, expr.base, scope)
    info = type_info(gen, base_type)
    check_field_access(gen, base_type, info.fields[index])
    stamp(expr, field_index=index, field_type=field_type, sie_type=field_type)
    return index, field_type


def checked_variable(type_name: str, *, moved: bool = False,
                     initialized: bool = True) -> Variable:
    """A scope entry carrying only its semantic type."""
    return Variable(None, type_name, moved=moved, initialized=initialized)


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

        from siec.codegen.slots import check_slot_function

        if check_slot_function(gen, fn):
            gen.checked_functions.add(symbol)
            return

        scope = {}
        for param in fn.params:
            scope[param.name] = checked_variable(param.type)
            if param.pattern is not None:
                bind_param_pattern(gen, param, scope)

        params = {
            param.name: scope[param.name]
            for param in fn.params
        }
        from siec.codegen.ownership import assign_adopts_parameter

        for position, param in enumerate(fn.params):
            if not assign_adopts_parameter(gen, fn, position):
                check_owned_cleanup(gen, param.name, scope)
        terminates = check_block(gen, fn.body or [], scope, fn)

        from siec.codegen.results import check_results

        check_results(gen, fn, params)

        from siec.codegen.nulls import check_nulls

        check_nulls(gen, fn, params)

        if (fn.return_type is not None and not fn.noreturn
                and not fn.returns_self and not terminates):
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
        initialized = [
            path[name].initialized for path in paths if name in path
        ]
        if states:
            parent[name] = checked_variable(
                variable.type,
                moved=any(states),
                # "May be initialized" is the safe merge for const locals:
                # another store could otherwise mutate it on one path.
                initialized=any(initialized),
            )


def check_owned_cleanup(gen: CodeGenerator, name: str, scope: dict) -> None:
    """Resolve the destructor an owned scope binding will invoke."""
    from siec.codegen.ownership import destroyable

    if destroyable(gen, scope[name].type):
        call = MethodCall(Var(name), "destroy", [])
        check_expression(gen, call, scope)
        plans = getattr(gen, "drop_call_plans", None)
        if plans is None:
            plans = gen.drop_call_plans = {}
        plans[strip_const(scope[name].type)] = call.call_plan


def check_temporary_cleanup(gen: CodeGenerator, type_name: str | None,
                            scope: dict) -> None:
    """Resolve cleanup for a destructible rvalue without a source name."""
    from siec.codegen.ownership import destroyable

    if not destroyable(gen, type_name):
        return
    name = ".temporary.drop"
    inner = dict(scope)
    inner[name] = checked_variable(strip_const(type_name))
    call = MethodCall(Var(name), "destroy", [])
    check_expression(gen, call, inner)
    plans = getattr(gen, "drop_call_plans", None)
    if plans is None:
        plans = gen.drop_call_plans = {}
    plans[strip_const(type_name)] = call.call_plan


def consume_owned_expression(gen: CodeGenerator, expr, type_name: str | None,
                             scope: dict) -> None:
    """Transfer a destructible value expression into a new owner."""
    from siec.codegen.ownership import (
        destroyable,
        expression_returns_reference,
        inherit_expression_identity,
    )
    from siec.codegen.interfaces import type_implements

    if isinstance(expr, Move):
        return
    source = expr
    if not destroyable(gen, type_name):
        return
    if getattr(expr, "self_transfer", False):
        return
    if expression_returns_reference(gen, expr):
        value_type = strip_const(strip_reference(type_name))
        if not type_implements(gen, value_type, "Clone"):
            raise TypeError(
                f"cannot copy owned {value_type!r} value through a reference: "
                "implement Clone")
        if getattr(expr, "owned_copy", None) is None:
            copied = inherit_expression_identity(
                expr, MethodCall(expr, "clone", []))
            check_expression(gen, copied, scope, value_type)
            expr.owned_copy = copied
        return
    if isinstance(source, Var) and source.name in scope:
        variable = scope[source.name]
        if variable.moved:
            raise TypeError(f"use of moved value {source.name!r}")
        scope[source.name] = checked_variable(variable.type, moved=True)
        return
    result_base = source.base if isinstance(source, Member) else None
    while (isinstance(result_base, Member)
           and result_base.field.startswith("#")):
        result_base = result_base.base
    if (isinstance(source, Member)
            and source.field in ("value", "error")
            and isinstance(result_base, Var)
            and result_base.name in scope):
        from siec.codegen.inference import result_arms

        base = result_base.name
        variable = scope[base]
        arms = result_arms(expand_alias(gen, variable.type))
        valid = (
            arms is not None
            and (source.field == "error" or arms[0] is not None)
        )
        if valid and destroyable(gen, variable.type):
            if variable.moved:
                raise TypeError(f"use of moved value {base!r}")
            scope[base] = checked_variable(variable.type, moved=True)
            return
    if isinstance(source, (Member, Index)):
        raise TypeError("cannot move part of an owned value; move the whole "
                        "local variable")


def declared_bound(fn: Function, type_name: str | None,
                   required: str) -> bool:
    """Whether this generic body's own bounds prove an interface claim."""
    placeholder = strip_const(strip_reference(type_name or ""))
    parameters = {
        *(fn.receiver_params or ()),
        *(fn.type_params or ()),
        *(fn.receiver_constraints or {}),
        *(fn.constraints or {}),
    }
    if placeholder not in parameters:
        return False

    for constraints in (fn.receiver_constraints, fn.constraints):
        bound = (constraints or {}).get(placeholder)
        bounds = bound if isinstance(bound, tuple) else (bound,)
        if required in bounds:
            return True
    return False


def check_statement(gen: CodeGenerator, stmt, scope: dict, fn: Function, *,
                    loop: bool = False,
                    emit_type: str | None | object = NO_EMIT) -> bool:
    """Check one statement, returning whether it terminates its path."""
    with source_location(line=getattr(stmt, "line", 0)):
        if line := getattr(stmt, "line", 0):
            gen.current_line = line

        if isinstance(stmt, LocalFunction):
            from siec.codegen.closures import check_closure

            scope[stmt.name] = checked_variable(
                check_closure(gen, stmt.value, scope))
            return False

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
            implicitly_initialized = False
            if (sized := sized_array(type_name)) is not None:
                from siec.codegen.enums import evaluate_size

                evaluate_size(gen, sized[1])
                type_name = sized[0]
                implicitly_initialized = True

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
                # A const binding is a read-only, non-owning value view. It
                # never takes the source's destruction responsibility.
                ownership_type = (type_name if is_const(type_name)
                                  else checked or type_name)
                consume_owned_expression(
                    gen, stmt.value, ownership_type, scope)
            else:
                check_type_defaults(gen, type_name)
                implicitly_initialized = type_has_defaults(gen, type_name)
            scope[stmt.name] = checked_variable(
                type_name,
                initialized=(stmt.value is not None or implicitly_initialized),
            )
            check_owned_cleanup(gen, stmt.name, scope)
            return False

        if isinstance(stmt, LetTuple):
            value_type = check_expression(gen, stmt.value, scope)
            stmt.pattern_types = bind_tuple_pattern(
                gen, stmt.pattern, value_type, scope)
            return False

        if isinstance(stmt, (Assign, MemberAssign, RefAssign, IndexAssign)):
            target = assignment_target(stmt)
            from siec.codegen.macros import macro_place, macro_view

            if (macro := macro_place(gen, target, scope)) is not None:
                name, expansion = macro
                stmt.macro_name = name
                stmt.macro_target = expansion
                with macro_view(gen, name):
                    try:
                        mutable_lvalue_type(gen, expansion, scope)
                    except (NameError, TypeError):
                        raise TypeError(
                            f"macro {name!r} does not expand to an "
                            "assignable place") from None
            const_init = (
                isinstance(target, Var)
                and target.name in scope
                and is_const(scope[target.name].type)
                and not scope[target.name].initialized
            )
            target_type = mutable_lvalue_type(
                gen, target, scope, allow_const_init=const_init)
            stmt.const_init = const_init
            from siec.codegen.assignment import (
                AssignmentAction,
                assignment_action,
                initializes_uninitialized_member,
            )

            stmt.initialization = initializes_uninitialized_member(
                target, scope)
            item_setter = None
            if isinstance(target, Index):
                from siec.codegen.inference import item_call

                item_setter = item_call(
                    gen, target, scope, "set_item", stmt.value)
            action = (
                AssignmentAction(None, stmt.value)
                if stmt.initialization
                else assignment_action(
                    gen, target, target_type, stmt.value, scope)
            )
            if item_setter is not None:
                check_expression(gen, item_setter, scope)
                stmt.item_set_call = item_setter
                target.item_set_call = item_setter
            stmt.assignment_action = action
            if item_setter is None:
                if action.call is not None:
                    check_expression(gen, action.call, scope)
                else:
                    check_expression(gen, action.value, scope, target_type)
                    consume_owned_expression(
                        gen, action.value, target_type, scope)

            if isinstance(target, Var) and target.name in scope:
                variable = scope[target.name]
                scope[target.name] = checked_variable(
                    variable.type, moved=False, initialized=True)
            return False

        if isinstance(stmt, CompoundAssign):
            from siec.codegen.macros import macro_place

            if (macro := macro_place(gen, stmt.target, scope)) is not None:
                stmt.macro_name, stmt.macro_target = macro
            target_type = mutable_lvalue_type(gen, stmt.target, scope)
            replacement = BinaryOp(stmt.op, stmt.target, stmt.value)
            if isinstance(stmt.target, Index):
                from siec.codegen.inference import item_call

                setter = item_call(
                    gen, stmt.target, scope, "set_item", replacement)
                if setter is not None:
                    from siec.codegen.assignment import AssignmentAction

                    check_expression(gen, setter, scope)
                    stmt.item_get_call = getattr(
                        stmt.target, "item_get_call", None)
                    stmt.item_set_call = setter
                    stmt.compound_call = None
                    stmt.compound_action = AssignmentAction(None, replacement)
                    return False

            from siec.codegen.methods import resolve_method
            from siec.codegen.statements import COMPOUND_METHODS

            method = COMPOUND_METHODS.get(stmt.op)
            concrete = strip_const(target_type)
            if (method is not None
                    and (concrete in gen.structs
                         or concrete.endswith("[]"))
                    and resolve_method(gen, concrete, method) is not None):
                call = MethodCall(stmt.target, method, [stmt.value])
                check_expression(gen, call, scope)
                stmt.compound_call = call
                stmt.compound_action = None
                return False

            from siec.codegen.assignment import assignment_action

            action = assignment_action(
                gen, stmt.target, target_type, replacement, scope)
            stmt.compound_call = None
            stmt.compound_action = action
            if action.call is not None:
                check_expression(gen, action.call, scope)
            else:
                check_expression(gen, action.value, scope, target_type)
                consume_owned_expression(
                    gen, action.value, target_type, scope)
            return False

        if isinstance(stmt, Return):
            if fn.noreturn:
                raise TypeError(f"'@noreturn' function {fn.name!r} "
                                "cannot return")
            if fn.returns_self:
                if (stmt.value is not None
                        and not (isinstance(stmt.value, Var)
                                 and stmt.value.name == "self")):
                    raise TypeError("a self-returning method can only return "
                                    "self")
                return True
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
            # A reference return borrows a place; it does not move that
            # place's owned value out of its container. The stripped type is
            # still the right checking context for implicit reference
            # binding, but only a by-value result transfers ownership.
            if not is_reference(return_type) and not is_const(return_type):
                consume_owned_expression(
                    gen, stmt.value, strip_reference(return_type), scope)
            return True

        if isinstance(stmt, ExprStmt):
            # a statement calling an '@noreturn' function ends its path:
            # the resolved callee decides, so a generic 'panic' instance
            # or a picked overload terminates like a concrete call
            gen.checked_call = None
            result = check_expression(gen, stmt.expr, scope)
            check_temporary_cleanup(gen, result, scope)
            from siec.codegen.ownership import (destroyable,
                                               manually_destroyed_local)

            if (name := manually_destroyed_local(stmt.expr)) in scope:
                variable = scope[name]
                if destroyable(gen, variable.type):
                    scope[name] = checked_variable(
                        variable.type, moved=True)
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
            check_truth(gen, stmt.condition, scope)
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
            runtime_type_case = False
            if isinstance(stmt.subject, TypeOf):
                from siec.codegen.expressions import type_operand
                from siec.codegen.statements import expand_when_interface

                subject_type = infer_type(gen, stmt.subject.value, scope)
                runtime_type_case = (
                    strip_const(strip_reference(subject_type or "")) == "Any"
                )

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
                previous_guard = gen.runtime_type_guard
                if runtime_type_case:
                    gen.runtime_type_guard = getattr(
                        arm, "runtime_interface_type", None)
                try:
                    terminates = check_block(
                        gen, arm.body, arm_scope, fn,
                        loop=loop, emit_type=emit_type)
                finally:
                    gen.runtime_type_guard = previous_guard
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
            check_truth(gen, stmt.condition, scope)
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
            check_truth(gen, stmt.condition, inner)
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

        if isinstance(stmt, Drop):
            target_type = mutable_lvalue_type(gen, stmt.target, scope)
            from siec.codegen.ownership import destroyable

            if isinstance(stmt.target, Index):
                from siec.codegen.inference import item_call

                if item_call(
                        gen, stmt.target, scope, "get_item") is not None:
                    raise TypeError("cannot drop a trait-indexed value: "
                                    "the container owns its element")

            bounded_destroy = declared_bound(fn, target_type, "Destroy")
            if not destroyable(gen, target_type) and not bounded_destroy:
                raise TypeError(
                    f"cannot drop {target_type!r}: the type does not "
                    "implement Destroy")
            # A placeholder's concrete destroy method is selected when its
            # bounded receiver family is instantiated; there is no method
            # symbol for the bare T during template-body checking.
            if not bounded_destroy:
                call = MethodCall(stmt.target, "destroy", [])
                check_expression(gen, call, scope)
                stmt.drop_call = call
            if isinstance(stmt.target, Var) and stmt.target.name in scope:
                variable = scope[stmt.target.name]
                if not is_reference(variable.type):
                    scope[stmt.target.name] = checked_variable(
                        variable.type, moved=True)
            return False

        if isinstance(stmt, Emit):
            if emit_type is NO_EMIT:
                raise TypeError("'emit' outside a block expression")
            if emit_type is None:
                raise TypeError("nothing here takes a value: the result this "
                                "'try' unwraps carries only an error")
            check_expression(gen, stmt.value, scope, emit_type)
            consume_owned_expression(gen, stmt.value, emit_type, scope)
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
        return check_member_field(gen, expr, scope)[1]

    if not (isinstance(expr, (Index, Call, MethodCall))
            or isinstance(expr, UnaryOp) and expr.op == "*"):
        raise TypeError("expression is not assignable")

    type_name = expr_sie_type(gen, expr, scope)
    if type_name is None:
        if (reason := untyped_reason(gen, expr, scope)) is not None:
            raise reason
        raise TypeError("expression is not assignable")
    return type_name


def mutable_lvalue_type(gen: CodeGenerator, expr: Expr, scope: dict, *,
                        allow_const_init: bool = False) -> str:
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
            return mutable_lvalue_type(
                gen, expansion, scope, allow_const_init=allow_const_init)

    if isinstance(expr, Var):
        if expr.name in scope:
            declared = scope[expr.name].type
            if is_const(declared) and not allow_const_init:
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
        declared = check_member_field(gen, expr, scope)[1]
        if is_const(declared):
            raise TypeError(f"cannot assign to const field {expr.field!r}")
        reject_const_base(gen, scope, expr.base)
        return declared

    if isinstance(expr, Index):
        check_expression(gen, expr.base, scope)
        reject_const_base(gen, scope, expr.base)
    elif isinstance(expr, UnaryOp) and expr.op == "*":
        check_expression(gen, expr.operand, scope)
        reject_const_base(gen, scope, expr.operand)
    elif isinstance(expr, Cast):
        declared = expr_sie_type(gen, expr, scope)
        if is_const(declared):
            raise TypeError("cannot assign through a const cast")
        reject_const_base(gen, scope, expr.operand)
    elif isinstance(expr, (Call, MethodCall)):
        # A call used only as a place does not pass through the ordinary
        # expression checker. Check it here so Emit receives its call plan.
        check_expression(gen, expr, scope)
        from siec.codegen.ownership import expression_returns_reference

        if not expression_returns_reference(gen, expr):
            raise TypeError("cannot take the address of a call's value")
        declared = expr_sie_type(gen, expr, scope)
        if is_const(declared):
            raise TypeError(
                f"cannot assign through a {declared!r} reference")

    return lvalue_type(gen, expr, scope)


def bind_tuple_pattern(gen: CodeGenerator, pattern: list,
                       type_name: str | None, scope: dict) -> list:
    """Bind a tuple pattern and return its resolved element-type tree."""
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

    pattern_types = []
    for name, arg in zip(pattern, args):
        if isinstance(name, list):
            pattern_types.append(bind_tuple_pattern(gen, name, arg, scope))
        else:
            scope[name] = checked_variable(arg)
            pattern_types.append(arg)

    return pattern_types


def bind_param_pattern(gen: CodeGenerator, param, scope: dict) -> None:
    """
    Bind a destructured by-value tuple parameter's pattern names into
    scope. The synthetic param.name remains for ownership of the tuple.
    """
    if is_reference(param.type):
        raise TypeError(
            f"cannot destructure a {param.type!r} parameter: "
            "tuple patterns take the argument by value")

    param.pattern_types = bind_tuple_pattern(
        gen, param.pattern, param.type, scope)


def check_foreach(gen: CodeGenerator, stmt: Foreach, scope: dict,
                  fn: Function, emit_type: str | None) -> None:
    """Resolve iterator methods and check a foreach body semantically."""
    from siec.codegen.methods import iteration_getter, resolve_method

    source_type = check_expression(gen, stmt.iterable, scope)
    source = strip_reference(source_type) if source_type else None
    if not source:
        raise TypeError("cannot iterate: the expression has no type")

    if (getter := iteration_getter(gen, source)) is not None:
        call = MethodCall(stmt.iterable, getter, [])
        it_type = check_expression(gen, call, scope)
        stmt.iterator_call = call
    elif resolve_method(gen, source, "has_next") is not None:
        it_type = strip_const(source)
        stmt.iterator_call = None
    else:
        raise TypeError(f"cannot iterate a {source_type!r} value: it is "
                        "neither an Iterable nor an Iterator")

    has_next = resolve_method(gen, it_type, "has_next")
    next_ = resolve_method(gen, it_type, "next")
    if has_next is None or next_ is None:
        raise TypeError(f"cannot iterate: type {it_type!r} has no "
                        f"{'has_next' if has_next is None else 'next'!r} "
                        "method")

    iterator_scope = dict(scope)
    iterator_scope["__foreach_it"] = checked_variable(it_type)
    has_next_call = Call(has_next, [Var("__foreach_it")])
    next_call = Call(next_, [Var("__foreach_it")])
    check_call(
        gen, has_next_call, iterator_scope, "bool", resolved=has_next)
    check_call(gen, next_call, iterator_scope, None, resolved=next_)
    stmt.iterator_type = it_type
    stmt.has_next_call = has_next_call
    stmt.next_call = next_call

    next_symbol = getattr(next_call, "resolved_symbol", None)
    next_ret = gen.return_types.get(next_symbol)
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
    """
    Check an expression against an optional expected type.

    Stamps typed-HIR fields on ``expr`` so emission can reuse the decisions
    instead of re-inferring them.
    """
    from siec.codegen.hir import annotate_result

    if expr is None:
        return None

    from siec.codegen.resolution import expression_view

    # A macro argument still belongs to the file where its caller wrote it.
    # Apply that view to the whole semantic operation, including private-field
    # checks and alias expansion, rather than only to qualified-name lookup.
    with expression_view(gen, expr):
        result = _check_expression(gen, expr, scope, expected)
        # Some expression checks return the contextual type after they check
        # their children.  Record an identity plan on that outer expression
        # too.  This gives Emit one complete instruction for every contextual
        # expression, including calls whose declared type equals the target.
        if (expected is not None and result is not None
                and getattr(expr, "coercion_plan", None) is None):
            require_fit(gen, expr, result, expected)
        return annotate_result(
            expr,
            result,
            expected,
            line=getattr(expr, "line", 0) or gen.current_line,
            file=gen.current_file,
        )


def check_truth(gen: CodeGenerator, expr: Expr, scope: dict) -> None:
    """Check one contextual truth test and resolve a struct's Truthy method."""
    actual = check_expression(gen, expr, scope)
    canonical = strip_const(strip_reference(actual)) if actual else None

    from siec.codegen.inference import type_info
    from siec.codegen.interfaces import intrinsic_truthy, type_implements

    if intrinsic_truthy(gen, canonical):
        return

    if type_info(gen, canonical) is not None and type_implements(
            gen, canonical, "Truthy"):
        from siec.codegen.methods import resolve_method

        symbol = resolve_method(gen, canonical, "truthy")
        if symbol is None:
            raise TypeError(
                f"type {canonical!r} implements 'Truthy' but has no "
                "'truthy' method")

        call = Call(symbol, [expr])
        result = check_call(gen, call, scope, "bool", resolved=symbol)
        if strip_const(result) != "bool":
            raise TypeError(
                f"type {canonical!r}'s 'truthy' method must return 'bool'")

        from siec.codegen.hir import stamp

        stamp(
            expr,
            truthy_symbol=call.call_plan.symbol,
            truthy_plan=call.call_plan,
            overwrite=True,
        )
        return

    raise TypeError(f"cannot test a value of type {actual or '?'} for truth")


def _check_expression(gen: CodeGenerator, expr: Expr | None, scope: dict,
                      expected: str | None = None) -> str | None:
    """Check an expression and return its inferred Sie type."""
    if expr is None:
        return None

    if isinstance(expr, ClosureExpr):
        from siec.codegen.closures import check_closure

        actual = check_closure(gen, expr, scope)
        if expected is not None and strip_const(expected) != actual:
            raise TypeError(f"cannot use a {actual!r} value where "
                            f"{expected!r} is expected")
        if expected is not None:
            require_fit(gen, expr, actual, expected)
        return actual

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

        from siec.codegen.macros import resolve_macro_use

        if (use := resolve_macro_use(gen, expr, scope)) is not None:
            return check_call(gen, use.call, scope, expected)

        if expr.type_args is not None:
            template = reference_template(gen, expr.name)
            if template is not None:
                instantiate_function(gen, template, expr.type_args)
                return reference_type(gen, expr)

        if (expected is not None
                and strip_const(expected).startswith("fn(")):
            module_file = getattr(expr, "module_file", None)
            with gen.in_file(module_file or gen.current_file):
                reference = reference_for_target(gen, expr, expected)
            if reference is not None:
                from siec.codegen.hir import CoercionPlan, stamp

                symbol = getattr(reference, "name", reference)
                stamp(
                    expr,
                    coercion_plan=CoercionPlan(
                        expected, expected, "function_reference",
                        symbol=symbol,
                    ),
                    coerce_to=expected,
                    coerce_kind="function_reference",
                    overwrite=True,
                )
                return expected

        const = (find_constant(
            gen,
            expr.name,
            getattr(expr, "module_file", None),
        ) if expr.name not in scope else None)
        if const is not None:
            with constant_view(gen, const):
                if const.type is not None:
                    check_expression(gen, const.value, scope, const.type)
                else:
                    return check_expression(gen, const.value, scope, expected)

    if isinstance(expr, MethodCall):
        from siec.codegen.methods import resolve_method

        receiver_type = check_expression(gen, expr.receiver, scope)
        from siec.codegen.deprecation import check_removed_method

        check_removed_method(gen, receiver_type, expr.method)
        symbol = resolve_method(gen, receiver_type, expr.method)
        if symbol is None:
            raise TypeError(
                f"type {receiver_type or '?'} has no method "
                f"{expr.method!r}")
        call = Call(symbol, list(expr.args), expr.type_args)
        result = check_call(
            gen,
            call,
            scope,
            expected,
            resolved=symbol,
            method_receiver=expr.receiver,
        )
        from siec.codegen.hir import stamp

        stamp(
            expr,
            resolved_symbol=getattr(call, "resolved_symbol", symbol),
            call_plan=getattr(call, "call_plan", None),
            overwrite=True,
        )
        if hasattr(call, "packed_variadic"):
            expr.packed_variadic = call.packed_variadic
        from siec.codegen.ownership import mark_self_returned_temporary

        mark_self_returned_temporary(gen, expr)
        return result

    if isinstance(expr, Member):
        base_type = strip_const(expr_sie_type(gen, expr.base, scope) or "")
        if base_type.startswith("closure fn(") and expr.field == "env":
            return "opaque*"

        from siec.codegen.resolution import fold_qualified

        if (folded := fold_qualified(gen, expr, scope)) is not None:
            result = check_expression(gen, folded, scope, expected)
            from siec.codegen.hir import copy_typed

            copy_typed(folded, expr)
            expr.qualified_value = folded
            return result

        from siec.codegen.types import raw_array

        if raw_array(base_type) is not None and expr.field == "length":
            if expected is not None:
                require_fit(gen, expr, "u64", expected)
            return expected or "u64"

        if base_type.startswith("Tuple<") and expr.field == "length":
            if expected is not None:
                require_fit(gen, expr, "u64", expected)
            return expected or "u64"

        check_member_field(gen, expr, scope)

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
        if target is not None:
            from siec.codegen.hir import CoercionPlan, stamp

            stamp(
                expr,
                coercion_plan=CoercionPlan(target, target, "block"),
                coerce_to=target,
                coerce_kind="block",
                overwrite=True,
            )
        return target

    if isinstance(expr, Try):
        check_expression(gen, expr.result, scope)
        result_type = expr_sie_type(gen, expr.result, scope)
        value_type, error_type = try_arms(gen, expr, scope)
        consume_owned_expression(gen, expr.result, result_type, scope)

        result_scope = dict(scope)
        result_scope["try.result"] = checked_variable(result_type)
        expr.ok_member = Member(Var("try.result"), "ok")
        check_expression(gen, expr.ok_member, result_scope, "bool")
        if value_type is not None:
            expr.value_member = Member(Var("try.result"), "value")
            check_expression(
                gen, expr.value_member, result_scope, value_type)
        expr.error_member = Member(Var("try.result"), "error")
        check_expression(gen, expr.error_member, result_scope, error_type)

        if value_type is None and expected is not None:
            from siec.codegen.inference import valueless_try

            raise valueless_try(result_type)
        if value_type is not None and expected is not None:
            require_fit(gen, expr, value_type, expected)

        if expr.propagates:
            check_try_propagation(gen, error_type)
            propagated_scope = dict(scope)
            propagated_scope["try.error"] = checked_variable(error_type)
            check_owned_cleanup(gen, "try.error", propagated_scope)
            returned = gen.return_types.get(gen.current_function)
            propagated = Call("Error", [Var("try.error")])
            check_expression(
                gen, propagated, propagated_scope, returned)
            expr.propagated_call = propagated
            consume_owned_expression(
                gen, propagated, returned, propagated_scope)
            return value_type

        inner = dict(scope)
        if expr.name is not None:
            inner[expr.name] = checked_variable(error_type)
            check_owned_cleanup(gen, expr.name, inner)

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
            from siec.codegen.aggregates import resolve_aggregate
            from siec.codegen.hir import CoercionPlan, stamp

            aggregate_plan = resolve_aggregate(gen, expr, expected)
            stamp(
                expr,
                aggregate_plan=aggregate_plan,
                coercion_plan=CoercionPlan(
                    None, expected, "aggregate",
                    const_target=is_const(expected),
                ),
                coerce_to=expected,
                coerce_kind="aggregate",
                overwrite=True,
            )
            for element in aggregate_plan.elements:
                check_expression(
                    gen, element.value, scope, element.target)
            for omitted in aggregate_plan.omitted:
                check_field_default(gen, omitted.field)
        return expected

    if isinstance(expr, ArrayLiteral):
        element = None
        if expected is not None:
            target = strip_const(expected)
            if target.endswith("[]"):
                element = target[:-2]
                kind = "array"
            elif target.endswith("*"):
                from siec.codegen.types import strip_nonnull

                element = strip_nonnull(target).removesuffix("*")
                kind = "array_literal_decay"
            else:
                kind = "array"
            from siec.codegen.hir import CoercionPlan, stamp

            stamp(
                expr,
                coercion_plan=CoercionPlan(None, expected, kind),
                coerce_to=expected,
                coerce_kind=kind,
                overwrite=True,
            )
        for value in expr.elements:
            check_expression(gen, value, scope, element)
        return expected or infer_type(gen, expr, scope)

    if isinstance(expr, TupleLiteral):
        element_types = None
        result = expr_sie_type(gen, expr, scope)
        if expected is not None:
            from siec.codegen.generics import split_generic

            parts = split_generic(strip_const(expected))
            if parts is not None and parts[0] == "Tuple":
                element_types = parts[1]
        elif result is not None:
            from siec.codegen.generics import split_generic

            parts = split_generic(strip_const(result))
            if parts is not None and parts[0] == "Tuple":
                element_types = parts[1]
        for index, value in enumerate(expr.elements):
            target = (element_types[index]
                      if element_types is not None
                      and index < len(element_types) else None)
            check_expression(gen, value, scope, target)
        if expected is not None:
            from siec.codegen.hir import CoercionPlan, stamp

            stamp(
                expr,
                coercion_plan=CoercionPlan(result, expected, "tuple"),
                coerce_to=expected,
                coerce_kind="tuple",
                overwrite=True,
            )
            return expected
        return result

    if isinstance(expr, Ternary):
        check_truth(gen, expr.condition, scope)
        if expected is not None:
            check_expression(gen, expr.then, scope, expected)
            check_expression(gen, expr.orelse, scope, expected)
            from siec.codegen.hir import CoercionPlan, stamp

            stamp(
                expr,
                coercion_plan=CoercionPlan(expected, expected, "identity"),
                coerce_to=expected,
                coerce_kind="identity",
                overwrite=True,
            )
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
        from siec.codegen.hir import CoercionPlan, stamp

        stamp(
            expr,
            coercion_plan=CoercionPlan(target, target, "identity"),
            coerce_to=target,
            coerce_kind="identity",
            overwrite=True,
        )
        return target

    if isinstance(expr, Cast):
        if not getattr(expr, "expanded", False):
            expr.type = expand_alias(gen, expr.type)
            expr.expanded = True
        validate_type(expr.type, gen.structs)
        contextual_operand = (
            expr.type
            if isinstance(
                expr.operand,
                (AggregateLiteral, ArrayLiteral, TupleLiteral, BlockExpr),
            )
            else None
        )
        operand_type = check_expression(
            gen, expr.operand, scope, contextual_operand)
        from siec.codegen.types import is_nonnull_pointer

        if (is_nonnull_pointer(expr.type)
                and not is_nonnull_pointer(operand_type)):
            raise TypeError(
                "an explicit cast cannot promise a non-null pointer; "
                "cast to a nullable pointer, then use postfix '!'")
        if (strip_const(expr.type) == "Any" and operand_type is not None
                and strip_const(strip_reference(operand_type)) != "Any"):
            wrapped = strip_const(strip_reference(operand_type))
            gen.any_types.setdefault(gen.current_function, set()).add(wrapped)
        if ((operand_type or "").startswith("closure fn(")
                and expr.type.startswith("fn(")):
            from siec.codegen.closures import validate_callback_adapter

            validate_callback_adapter(operand_type, expr.type)
        return expr.type

    if isinstance(expr, NullLiteral):
        # a bare function reference is a pointer at heart: 'null' clears
        # a callback the same way it clears any pointer target
        target = strip_const(expected) if expected is not None else None
        from siec.codegen.types import is_nonnull_pointer

        if is_nonnull_pointer(target):
            raise TypeError("null cannot initialize a non-null pointer")
        if (target is not None and not target.endswith("*")
                and not (target.startswith("fn(")
                         and not fn_type_parts(target)[2])):
            raise TypeError("'null' needs a pointer context")
        if expected is not None:
            require_fit(gen, expr, "opaque*", expected)
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
            expr.item_get_call = rewritten
            return check_expression(gen, rewritten, scope, expected)
        base_context = None
        if (expected is not None
                and isinstance(expr.base, (AggregateLiteral, ArrayLiteral))):
            base_context = f"{strip_const(expected)}[]"
        check_expression(gen, expr.base, scope, base_context)
        check_expression(gen, expr.index, scope)
        base_type = strip_const(expr_sie_type(gen, expr.base, scope) or "")
        if base_type.startswith("Tuple<"):
            from siec.codegen.expressions import tuple_element

            _, index, elements = tuple_element(gen, expr, scope)
            actual = elements[index]
        else:
            actual = expr_sie_type(gen, expr, scope) or infer_type(
                gen, expr, scope)
        if actual is None and expected is not None:
            actual = expected
        if expected is not None:
            require_fit(gen, expr, actual, expected)
            return expected
        return actual

    if isinstance(expr, Slice):
        from siec.codegen.inference import slice_call

        if (rewritten := slice_call(gen, expr, scope)) is not None:
            expr.slice_call = rewritten
            return check_expression(gen, rewritten, scope, expected)
        base_type = check_expression(gen, expr.base, scope, expected)
        if expr.start is not None:
            check_expression(gen, expr.start, scope, "u64")
        if expr.stop is not None:
            check_expression(gen, expr.stop, scope, "u64")
        actual = base_type or expr_sie_type(gen, expr, scope)
        if expected is not None:
            require_fit(gen, expr, actual, expected)
            return expected
        return actual

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
        from siec.codegen.inference import operator_call, option_none_test

        if (rewritten := option_none_test(gen, expr, scope)) is not None:
            return check_expression(gen, rewritten, scope, expected)

        if (rewritten := operator_call(gen, expr, scope)) is not None:
            expr.operator_rewrite = rewritten
            return check_expression(gen, rewritten, scope, expected)

        if expr.op in ("and", "or"):
            check_truth(gen, expr.left, scope)
            check_truth(gen, expr.right, scope)
            return "bool"

        # Pointer compatibility is a Sie type rule, not an LLVM lowering
        # detail. Check it here even when this function will not be emitted.
        from siec.codegen.types import strip_nonnull

        left_name = strip_const(strip_nonnull(expand_alias(
            gen, expr_sie_type(gen, expr.left, scope)
            or infer_type(gen, expr.left, scope), checked=False) or ""))
        right_name = strip_const(strip_nonnull(expand_alias(
            gen, expr_sie_type(gen, expr.right, scope)
            or infer_type(gen, expr.right, scope), checked=False) or ""))
        left_pointer = left_name.endswith("*") or left_name.startswith("fn(")
        right_pointer = right_name.endswith("*") or right_name.startswith("fn(")
        if ((left_pointer or right_pointer)
                and expr.op not in ("+", "-")
                and (not left_pointer or not right_pointer
                     or (left_name != right_name
                         and "opaque*" not in (left_name, right_name)))):
            raise TypeError(
                f"cannot apply {expr.op!r} to {left_name} and {right_name}")

    if isinstance(expr, UnaryOp) and expr.op == "not":
        check_truth(gen, expr.operand, scope)
        return "bool"

    if isinstance(expr, UnaryOp) and expr.op == "nonnull":
        from siec.codegen.types import nonnull_pointer, strip_nonnull

        actual = check_expression(gen, expr.operand, scope)
        nullable = strip_const(strip_nonnull(actual)) if actual else None
        if nullable is None or not nullable.endswith("*"):
            raise TypeError("postfix '!' requires a pointer operand")
        if isinstance(expr.operand, NullLiteral):
            raise TypeError("null cannot be converted to a non-null pointer")
        result = nonnull_pointer(actual)
        if expected is not None:
            require_fit(gen, expr, result, expected)
            return expected
        return result

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


def type_has_defaults(gen: CodeGenerator, type_name: str | None,
                      seen: set[str] | None = None) -> bool:
    """Whether a bare declaration receives a recursive struct default."""
    canonical = strip_const(type_name)
    info = gen.structs.get(canonical)
    if info is None or info.fields is None or info.is_union:
        return False

    seen = set() if seen is None else seen
    if canonical in seen:
        return False
    seen.add(canonical)
    return any(
        field.default is not None
        or type_has_defaults(gen, field.type, seen)
        for field in info.fields
    )


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
               resolved: str | None = None,
               method_receiver=None) -> str | None:
    """Resolve and check a call without emitting its instruction."""
    from siec.codegen.generics import instantiate_function, pick_call_candidate
    from siec.codegen.methods import (
        constructor_type,
        method_call,
        qualified_method,
        takes_receiver,
    )
    from siec.codegen.worklist import activate_function_instance
    from siec.codegen.hir import CallPlan, stamp

    written_call = call
    method_receiver = (method_receiver if method_receiver is not None
                       else getattr(call, "method_receiver", None))

    from siec.codegen.macros import resolve_macro_use

    if resolve_macro_use(gen, call, scope) is not None:
        from siec.codegen.macros import macro_expansion, macro_view

        expansion = macro_expansion(gen, call)
        with macro_view(gen, call.name):
            if isinstance(expansion, Block):
                target = expected or block_emit_type(gen, expansion.body, scope)
                check_block_expression(
                    gen,
                    BlockExpr(expansion.body),
                    scope,
                    target,
                )
                return expected or target
            return check_expression(gen, expansion, scope, expected)

    if call.name == "enumerate":
        from siec.codegen.methods import rewrite_enumerate

        if (rewritten := rewrite_enumerate(gen, call, scope)) is not None:
            result = check_expression(gen, rewritten, scope, expected)
            stamp(
                call,
                call_plan=CallPlan("rewrite", replacement=rewritten),
                sie_type=result,
                expected_type=expected,
                overwrite=True,
            )
            return result

    indirect = None
    indirect_symbol = None
    if call.name in scope:
        indirect = scope[call.name].type
    elif "." not in call.name and "::" not in call.name:
        global_symbol = gen.resolve_symbol(call.name)
        if global_symbol in gen.globals:
            indirect = gen.globals[global_symbol]
            indirect_symbol = global_symbol

    if indirect is not None:
        return check_indirect_call(
            gen, call, scope, indirect, symbol=indirect_symbol)

    symbol = resolved
    module = None
    if symbol is None and "::" in call.name:
        symbol = qualified_method(gen, call.name)
    elif symbol is None and "." in call.name:
        if (found := gen.resolve_member(call.name.split("."))) is not None:
            symbol, module = found
        if symbol is None and (found := method_call(gen, call, scope)):
            symbol, method_receiver = found
        if (symbol is not None and method_receiver is None
                and symbol in gen.globals):
            return check_indirect_call(
                gen, call, scope, gen.globals[symbol], symbol=symbol)
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
        symbol, module = gen.resolve_call_target(call.name)

    from siec.codegen.deprecation import check_removed

    check_removed(gen, symbol)

    kind, candidate = pick_call_candidate(
        gen, symbol, call, scope, expected, module=module,
        method_receiver=method_receiver)
    if kind == "generic":
        template, type_args = candidate
        symbol = instantiate_function(gen, template, type_args)
    else:
        symbol = candidate
    activate_function_instance(gen, symbol)

    passes_receiver = (
        method_receiver is not None and takes_receiver(gen, symbol))
    if passes_receiver:
        call = Call(
            call.name,
            [method_receiver, *call.args],
            call.type_args,
        )
    if method_receiver is not None:
        written_call.method_receiver = method_receiver

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

            # Rank constructor overloads the same way as ordinary calls. The
            # concrete side knows the receiver's type directly; the generic
            # side receives an equivalent synthetic borrowed expression.
            inner = dict(scope)
            inner[".ctor"] = checked_variable(f"&{ctor}")
            generic_call = Call(init, [Var(".ctor"), *call.args])
            init_kind, init_candidate = pick_call_candidate(
                gen, init, call, scope, receiver=ctor,
                generic_call=generic_call, generic_scope=inner)

            if init_kind == "generic":
                check_call(
                    gen,
                    generic_call,
                    inner,
                    resolved=init,
                )
                init = getattr(generic_call, "resolved_symbol", init)
            else:
                init = init_candidate
                activate_function_instance(gen, init)
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
                    init,
                    default_offset=default_offset,
                )
                if hasattr(call, "packed_variadic"):
                    written_call.packed_variadic = call.packed_variadic
                from siec.codegen.deprecation import note_use

                note_use(gen, init)
            gen.checked_call = None
            plan = CallPlan(
                "constructor",
                symbol=init,
                constructor_type=ctor,
            )
            stamp(
                call,
                call_plan=plan,
                sie_type=ctor,
                expected_type=expected,
                overwrite=True,
            )
            stamp(
                written_call,
                call_plan=plan,
                sie_type=ctor,
                expected_type=expected,
                overwrite=True,
            )
            return ctor
        if call.type_args is not None:
            raise TypeError(f"function {call.name!r} is not generic")
        raise NameError(f"undefined function {call.name!r}")

    if call.type_args is not None and kind != "generic":
        raise TypeError(f"function {call.name!r} is not generic")

    params = gen.param_types[symbol]
    check_call_arguments(
        gen,
        call,
        scope,
        params,
        symbol,
    )
    if hasattr(call, "packed_variadic"):
        written_call.packed_variadic = call.packed_variadic

    from siec.codegen.deprecation import note_use

    note_use(gen, symbol)
    gen.checked_call = symbol

    result = strip_reference(gen.return_types.get(symbol))
    plan = CallPlan(
        "direct",
        symbol=symbol,
        receiver=method_receiver,
        passes_receiver=passes_receiver,
    )
    stamp(call, resolved_symbol=symbol, call_plan=plan, sie_type=result,
          expected_type=expected, overwrite=True)
    stamp(written_call, resolved_symbol=symbol, call_plan=plan, sie_type=result,
          expected_type=expected, overwrite=True)
    return result


def check_indirect_call(gen: CodeGenerator, call: Call, scope: dict,
                        type_name: str, *, symbol: str | None = None
                        ) -> str | None:
    """Check a call through a function-typed variable or global."""
    type_name = strip_const(type_name)
    if not type_name.startswith(("fn(", "closure fn(")):
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
    check_call_arguments(gen, call, scope, params)
    gen.checked_call = None
    result = strip_reference(ret)
    from siec.codegen.hir import CallPlan, stamp

    stamp(
        call,
        call_plan=CallPlan(
            "indirect",
            indirect_type=type_name,
            indirect_symbol=symbol,
        ),
        sie_type=result,
        overwrite=True,
    )
    return result


def check_call_arguments(gen: CodeGenerator, call: Call, scope: dict,
                         params: list[str], symbol: str | None = None, *,
                         default_offset: int = 0) -> None:
    """Check call arity and each fixed argument against its parameter."""
    all_defaults, defaults_file = gen.param_defaults.get(
        symbol,
        ([], None),
    )
    defaults = all_defaults[default_offset:]
    arity = (gen.call_arities[symbol].without_prefix(default_offset)
             if symbol is not None else
             CallArity.exact(len(params)))
    count_error = arity.error(len(call.args))
    if count_error is not None:
        raise TypeError(
            f"{count_error} arguments to function {call.name!r}")

    fixed = len(params) - 1 if arity.variadic else len(params)
    for arg, param in zip(call.args[:fixed], params):
        if is_reference(param):
            check_reference_argument(gen, arg, param, scope)
        else:
            actual = check_expression(gen, arg, scope, param)
            if not is_const(param):
                consume_owned_expression(gen, arg, actual or param, scope)
    packed = None
    if arity.variadic:
        from siec.codegen.calls import pack_variadic

        packed = pack_variadic(gen, call, len(params), scope)

    for index, arg in enumerate(call.args[fixed:], start=fixed):
        forwarded = (
            packed is call and index == len(params) - 1
            and len(call.args) == len(params)
        )
        actual = check_expression(
            gen, arg, scope, params[-1] if forwarded else None)
        if arity.variadic and actual is not None:
            wrapped = strip_const(strip_reference(actual))
            if wrapped != "Any":
                gen.any_types.setdefault(gen.current_function, set()).add(
                    wrapped)

    if packed is not None and packed is not call:
        check_expression(gen, packed.args[-1], scope, params[-1])
        call.packed_variadic = packed

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
    if isinstance(arg, (Call, MethodCall, AggregateLiteral, ArrayLiteral)):
        check_temporary_cleanup(gen, referenced, scope)

    if declared is not None:
        if strip_const(declared) != strip_const(referenced):
            from siec.codegen.overloads import parameter_fit

            if (not is_const(referenced)
                    or parameter_fit(
                        gen, arg, declared, strip_const(referenced)) is None):
                raise TypeError(
                    f"cannot bind a {declared!r} value to a {param!r} "
                    "parameter")
            check_expression(gen, arg, scope, strip_const(referenced))
            return
        if is_const(declared) and not is_const(referenced):
            raise TypeError(
                f"cannot bind a {declared!r} value to a mutable "
                f"{param!r} parameter")

        # Type inference can identify a call's result without resolving the
        # function instance that produces it. Check the expression itself
        # before borrowing its storage so every constructor and generic call
        # needed by emission is present after the checking phase.
        check_expression(gen, arg, scope, referenced)

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
                expected: str):
    """Require a fit and record the exact implicit conversion operation."""
    from siec.codegen.overloads import parameter_fit
    from siec.codegen.types import is_nonnull_pointer, strip_nonnull
    from siec.codegen.hir import CoercionPlan, stamp

    source = strip_const(actual)
    target = strip_const(expected)

    def accept(kind, *, nested=None):
        plan = CoercionPlan(
            actual,
            expected,
            kind,
            nested=nested,
            const_target=is_const(expected),
        )
        stamp(
            expr,
            coerce_to=expected,
            coerce_kind=kind,
            coercion_plan=plan,
            overwrite=True,
        )
        return plan

    if parameter_fit(gen, expr, actual, expected) is not None:
        if (is_nonnull_pointer(target) and not is_nonnull_pointer(source)
                and strip_nonnull(source) == strip_nonnull(target)):
            expr.requires_nonnull = True
        if isinstance(expr, NullLiteral):
            return accept("null")
        if target == "opaque*" and (
                source.endswith("*") or source.endswith("[]")):
            return accept("opaque")
        if (source.endswith("[]")
                and strip_nonnull(target) == f"{source[:-2]}*"):
            return accept("array_decay")

        from siec.codegen.inference import enum_backing, numeric_class

        source_class = numeric_class(enum_backing(gen, source))
        target_class = numeric_class(enum_backing(gen, target))
        literal = isinstance(expr, (IntLiteral, SizeOf, TypeId)) or (
            isinstance(expr, FloatLiteral)
            and target_class is not None and target_class[0] == "f"
        )
        if literal and target_class is not None:
            return accept("adopt")
        if (source_class is not None and target_class is not None
                and source_class[1] < target_class[1]):
            return accept({
                "i": "sign_extend",
                "u": "zero_extend",
                "f": "float_extend",
            }[target_class[0]])
        return accept("identity")

    # a bare integer literal fills a 'char' or 'bool' target like the
    # small integer it emits as; overload ranking still prefers a numeric
    # candidate, so only the direct fit widens here
    if (strip_const(expected) in ("char", "bool")
            and isinstance(expr, IntLiteral)):
        return accept("adopt")

    if isinstance(expr, NullLiteral):
        # a bare function reference is a pointer at heart: 'null' clears
        # a callback the same way it clears any pointer target
        target = strip_const(expected)
        if target.startswith("fn(") and not fn_type_parts(target)[2]:
            return accept("null")
        raise TypeError("'null' needs a pointer context")

    from siec.codegen.inference import option_value

    # Any value fitting T fills an Option<T>; after flow checking has proven
    # presence, an Option<T> expression may in turn stand in for its T.
    if (carried := option_value(target)) is not None:
        nested = require_fit(gen, expr, actual, carried)
        expr.option_wrap_type = carried
        return accept("option_wrap", nested=nested)
    if (carried := option_value(source)) is not None:
        if strip_const(carried) != target:
            raise TypeError(f"cannot implicitly convert {actual} to {target}")
        expr.option_source_type = source
        expr.option_decay_type = carried
        return accept("option_decay")

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
