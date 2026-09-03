"""Emission of statements and control flow."""

from llvmlite import ir

from siec.ast import (
    Assign,
    BinaryOp,
    Block,
    Break,
    Call,
    Case,
    CompoundAssign,
    Continue,
    Defer,
    Drop,
    Emit,
    ExprStmt,
    For,
    Foreach,
    If,
    Index,
    IndexAssign,
    Let,
    LetTuple,
    LocalFunction,
    Member,
    MemberAssign,
    MethodCall,
    RefAssign,
    Return,
    TypeId,
    TypeOf,
    UnaryOp,
    Var,
    When,
    While,
)
from siec.codegen.aliases import expand_alias
from siec.codegen.coercion import emit_coerced
from siec.codegen.enums import evaluate_size
from siec.codegen.errors import source_location
from siec.codegen.macros import emit_macro_assignment, macro_place, macro_view
from siec.codegen.expressions import (
    emit_bool,
    emit_expression,
    emit_lvalue,
)
from siec.codegen.inference import (
    expr_sie_type,
    infer_type,
    untyped_reason,
)
from siec.codegen.generator import (
    CodeGenerator,
    Variable,
    entry_alloca,
    make_volatile,
)
from siec.codegen.lvalues import AddressLValue, ItemLValue, resolve_lvalue
from siec.codegen.types import (
    is_const,
    is_reference,
    resolve_type,
    sized_array,
    strip_const,
    strip_reference,
)


# the in-place method each compound assignment reaches for, when the
# target's type has one: 'a += b' is 'a.add_assign(b)'
COMPOUND_METHODS = {"+": "add_assign", "-": "sub_assign", "*": "mul_assign",
                    "/": "div_assign", "%": "rem_assign"}


def emit_full_expression(gen: CodeGenerator, builder: ir.IRBuilder, expr,
                         scope: dict, *, boolean: bool = False,
                         expected=None):
    """Emit one non-statement expression and end its temporary lifetime."""
    from siec.codegen.ownership import (begin_temporary_frame,
                                       finish_temporary_frame)

    owns = begin_temporary_frame(gen)
    if boolean:
        value = emit_bool(gen, builder, expr, scope)
    else:
        value = emit_expression(gen, builder, expr, expected, scope)
    finish_temporary_frame(gen, builder, owns)
    return value


def emit_block(gen: CodeGenerator, builder: ir.IRBuilder, stmts: list,
               scope: dict, initial_cleanups=None) -> None:
    """
    Emit statements in order, stopping once the current block is terminated.

    Each block is a defer scope: statements deferred inside it run when it
    falls off its end; a 'return' or 'emit' leaving it early flushes them
    itself, along the exiting path.
    """
    gen.defer_frames.append(list(initial_cleanups or ()))

    for stmt in stmts:
        emit_statement(gen, builder, stmt, scope)
        if builder.block.is_terminated:
            break

    if not builder.block.is_terminated:
        flush_defers(gen, builder, [gen.defer_frames[-1]])

    gen.defer_frames.pop()


def flush_defers(gen: CodeGenerator, builder: ir.IRBuilder, frames: list) -> None:
    """
    Run deferred statements along the current path: innermost frame first,
    each frame in reverse, last deferred first.

    The frames stay in place - other paths out of the same scopes flush
    their own copies.
    """
    gen.flushing_defers += 1
    gen.flush_loop_floors.append(len(gen.loop_targets))
    try:
        for frame in reversed(frames):
            for entry in reversed(frame):
                from siec.codegen.ownership import DropCleanup, emit_drop_cleanup

                if isinstance(entry, DropCleanup):
                    emit_drop_cleanup(gen, builder, entry)
                else:
                    stmt, snapshot = entry
                    emit_statement(gen, builder, stmt, snapshot)
    finally:
        gen.flush_loop_floors.pop()
        gen.flushing_defers -= 1


def volatile_store(gen: CodeGenerator, store) -> None:
    """
    Mark a store volatile when it writes a '@volatile' struct value.
    """
    if gen.volatile_struct(store.operands[0].type):
        make_volatile(store)


def emit_statement(gen: CodeGenerator, builder: ir.IRBuilder, stmt, scope: dict) -> None:
    """
    Emit a single statement into the builder's current block, tagging errors with its line.
    """
    with source_location(line=getattr(stmt, "line", 0)):
        # this statement's line locates whatever it uses, a deprecated
        # name included
        if line := getattr(stmt, "line", 0):
            gen.current_line = line

        # under '-g', instructions emitted from here carry this statement's line
        if gen.debug is not None and line:
            builder.debug_metadata = gen.debug.location(line)

        emit_statement_body(gen, builder, stmt, scope)


def emit_statement_body(gen: CodeGenerator, builder: ir.IRBuilder, stmt, scope: dict) -> None:
    """
    Emit a single statement into the builder's current block.
    """
    if isinstance(stmt, LocalFunction):
        from siec.codegen.closures import closure_type, emit_closure

        type_name = closure_type(stmt.value)
        value = emit_closure(gen, builder, stmt.value, scope)
        slot = entry_alloca(builder, value.type, stmt.name)
        builder.store(value, slot)
        scope[stmt.name] = Variable(slot, type_name)
    elif isinstance(stmt, Let):
        # an unannotated 'let' takes its type from its initializer
        if not getattr(stmt, "expanded", False):
            stmt.type = expand_alias(gen, stmt.type)
            stmt.expanded = True
        type_name = stmt.type
        if type_name is None:
            type_name = infer_type(gen, stmt.value, scope)
            if type_name is None:
                # an unknown name or a valueless call is the real story
                if (reason := untyped_reason(gen, stmt.value, scope)) is not None:
                    raise reason

                raise TypeError(f"cannot infer a type for {stmt.name!r}: "
                                "annotate it explicitly")

        # references only pass parameters; a variable is its own storage
        if is_reference(type_name):
            raise TypeError("a reference cannot type a variable")

        # a sized array 'X[N]' declares an 'X[]' backed by N stack elements
        if (sized := sized_array(type_name)) is not None:
            emit_sized_array_let(gen, builder, stmt, sized, scope)
            return

        # Emit the initializer against the surrounding scope first so a
        # shadowing 'let a = a + 1' still reads the outer 'a'. Only then
        # reserve the new slot and install the binding.
        from siec.codegen.ownership import (DropCleanup, begin_temporary_frame,
                                           consume_temporary, destroyable,
                                           disarm_expression,
                                           finish_temporary_frame,
                                           new_drop_flag, set_drop_flag)

        initial = None
        owns = False
        if stmt.value is not None:
            owns = begin_temporary_frame(gen)
            initial = emit_coerced(
                gen, builder, stmt.value, type_name, scope)
            if not is_const(type_name):
                consume_temporary(gen, stmt.value)
                disarm_expression(gen, builder, stmt.value, scope)

        var_type = resolve_type(type_name, gen.structs)
        slot = entry_alloca(builder, var_type, stmt.name)

        # an '@align(N)' struct's slot honors the declared alignment
        if (align := gen.struct_align(type_name)) is not None:
            slot.align = align

        owned = destroyable(gen, type_name)
        drop_flag = new_drop_flag(builder, stmt.name) if owned else None
        scope[stmt.name] = Variable(
            slot, type_name, drop_flag=drop_flag)
        if owned:
            gen.defer_frames[-1].append(
                DropCleanup(stmt.name, scope[stmt.name]))

        if gen.debug is not None:
            gen.debug.declare_variable(builder, slot, stmt.name, type_name, stmt.line)

        if initial is not None:
            volatile_store(gen, builder.store(initial, slot))
            set_drop_flag(builder, scope[stmt.name], True)
            finish_temporary_frame(gen, builder, owns)
        else:
            # a bare declaration of a struct with field defaults starts
            # from them, its undefaulted fields zeroed
            from siec.codegen.expressions import default_value

            if (default := default_value(gen, builder, type_name)) is not None:
                volatile_store(gen, builder.store(default, slot))
                set_drop_flag(builder, scope[stmt.name], True)
    elif isinstance(stmt, LetTuple):
        emit_let_tuple(gen, builder, stmt, scope)
    elif isinstance(stmt, CompoundAssign):
        from siec.codegen.ownership import (begin_temporary_frame,
                                           finish_temporary_frame)

        owns = begin_temporary_frame(gen)
        emit_compound_assign(gen, builder, stmt, scope)
        finish_temporary_frame(gen, builder, owns)
    elif isinstance(stmt, (Assign, MemberAssign, RefAssign, IndexAssign)):
        from siec.codegen.ownership import (begin_temporary_frame,
                                           finish_temporary_frame)

        owns = begin_temporary_frame(gen)
        emit_assignment(gen, builder, stmt, scope)
        finish_temporary_frame(gen, builder, owns)
    elif isinstance(stmt, Block):
        # a block runs in a child scope: writes to outer variables persist
        # through their shared slots, while inner declarations end with it
        emit_block(gen, builder, stmt.body, dict(scope))
    elif isinstance(stmt, If):
        emit_if(gen, builder, stmt, scope)
    elif isinstance(stmt, Case):
        emit_case(gen, builder, stmt, scope)
    elif isinstance(stmt, While):
        emit_while(gen, builder, stmt, scope)
    elif isinstance(stmt, For):
        emit_for(gen, builder, stmt, scope)
    elif isinstance(stmt, Foreach):
        emit_foreach(gen, builder, stmt, scope)
    elif isinstance(stmt, Defer):
        # a defer's own block cannot re-defer: the frame it would join is
        # the one being flushed; a scope of its own inside it can
        inner = stmt.stmt.body if isinstance(stmt.stmt, Block) else []
        if any(isinstance(s, Defer) for s in inner):
            raise TypeError("a defer cannot hold another defer directly; "
                            "give it a scope of its own")

        # capture the statement with the scope as it stands: the shared
        # slots make later writes visible when it finally runs, while
        # later shadowing declarations stay out of sight
        gen.defer_frames[-1].append((stmt.stmt, dict(scope)))
    elif isinstance(stmt, Drop):
        emit_expression(gen, builder, stmt.drop_call, None, scope)
        if isinstance(stmt.target, Var) and stmt.target.name in scope:
            from siec.codegen.ownership import set_drop_flag

            set_drop_flag(builder, scope[stmt.target.name], False)
    elif isinstance(stmt, Emit):
        # store the value into the enclosing block expression's slot and
        # jump past the block, ending it early like a return ends a function
        if gen.flushing_defers:
            raise TypeError("a deferred statement cannot emit")

        if not gen.emit_targets:
            raise TypeError("'emit' outside a block expression")

        slot, end_block, target_name, depth = gen.emit_targets[-1]

        # a 'try' over a result carrying only an error has no value for
        # its arm to stand in for
        if slot is None:
            raise TypeError("nothing here takes a value: the result this "
                            "'try' unwraps carries only an error")

        if target_name is not None:
            value = emit_coerced(gen, builder, stmt.value, target_name, scope)
        else:
            value = emit_expression(gen, builder, stmt.value, slot.type.pointee, scope)

        # the value is computed before the scopes being left run their defers
        builder.store(value, slot)
        from siec.codegen.ownership import (consume_temporary,
                                           disarm_expression)

        consume_temporary(gen, stmt.value)
        disarm_expression(gen, builder, stmt.value, scope)
        flush_defers(gen, builder, gen.defer_frames[depth:])
        builder.branch(end_block)
    elif isinstance(stmt, (Break, Continue)):
        word = "break" if isinstance(stmt, Break) else "continue"

        if not gen.loop_targets:
            raise TypeError(f"'{word}' outside a loop")

        # a deferred statement may only steer a loop of its own, entered
        # above the flush's floor, never the one it flushes inside of
        if (gen.flushing_defers
                and len(gen.loop_targets) <= gen.flush_loop_floors[-1]):
            raise TypeError(f"a deferred statement cannot {word}")

        break_block, continue_block, depth = gen.loop_targets[-1]

        # the scopes being left run their defers along the exiting path
        flush_defers(gen, builder, gen.defer_frames[depth:])
        builder.branch(break_block if isinstance(stmt, Break) else continue_block)
    elif isinstance(stmt, Return):
        from siec.codegen.overloads import display_name

        # an '@noreturn' function promises to never give control back
        if builder.function.name in gen.noreturns:
            name = display_name(builder.function.name)
            raise TypeError(f"'@noreturn' function {name!r} cannot return")

        # a deferred statement runs on the way out of a scope; returning
        # there would flush the very frame holding it
        if gen.flushing_defers:
            raise TypeError("a deferred statement cannot return")

        # A self return always yields the receiver address. The checker only
        # permits a bare return or an explicit `return self` here.
        if builder.function.name in gen.self_returns:
            flush_defers(gen, builder, gen.defer_frames)
            builder.ret(scope["self"].slot)
            return

        if stmt.value is None:
            flush_defers(gen, builder, gen.defer_frames)

            # a bare 'return' in main yields its implicit exit code 0: only
            # main is declared without a return type yet lowered to i32
            ret_type = builder.function.function_type.return_type
            if (not isinstance(ret_type, ir.VoidType)
                    and gen.return_types.get(builder.function.name) is None):
                builder.ret(ir.Constant(ret_type, 0))
            else:
                builder.ret_void()
        else:
            # a function without a return type has nothing to return; main
            # is the exception, lowered to i32 with an implicit 0
            ret_type = gen.return_types[builder.function.name]
            if ret_type is None and isinstance(
                    builder.function.function_type.return_type, ir.VoidType):
                name = display_name(builder.function.name)
                raise TypeError(f"function {name!r} has no return type and "
                                "cannot return a value")

            # a '&T' return yields the value's address, which must be
            # assignable storage of exactly the referenced type
            if is_reference(ret_type):
                referenced = strip_reference(ret_type)
                returned = stmt.value
                value_type = expr_sie_type(gen, returned, scope)

                # A checked Option<T> decaying into a reference return borrows
                # its inline value field. Extracting the Option as a value
                # would create a temporary and could not yield a stable &T.
                if getattr(returned, "option_decay_type", None) is not None:
                    returned = Member(returned, "value")
                    value_type = getattr(stmt.value, "option_decay_type")

                if (value_type is not None
                        and strip_const(value_type) != strip_const(referenced)):
                    raise TypeError(f"cannot return a {value_type!r} value "
                                    f"as {ret_type!r}")

                value = emit_lvalue(gen, builder, returned, scope)
                flush_defers(gen, builder, gen.defer_frames)
                builder.ret(value)
                return

            # the return value is computed before any deferred statement runs
            from siec.codegen.ownership import (begin_temporary_frame,
                                               consume_temporary,
                                               finish_temporary_frame)

            owns = begin_temporary_frame(gen)
            value = emit_coerced(gen, builder, stmt.value, ret_type, scope)
            if not is_const(ret_type):
                consume_temporary(gen, stmt.value)
            from siec.codegen.ownership import disarm_expression

            if not is_const(ret_type):
                disarm_expression(gen, builder, stmt.value, scope)
            finish_temporary_frame(gen, builder, owns)
            flush_defers(gen, builder, gen.defer_frames)
            builder.ret(value)
    elif isinstance(stmt, ExprStmt):
        # a statement calling a macro splices its block in place; one
        # without an 'emit' has no value to discard, and is fine here
        from siec.codegen.macros import resolve_macro_use

        if (isinstance(stmt.expr, Call)
                and resolve_macro_use(gen, stmt.expr, scope) is not None):
            from siec.codegen.macros import macro_expansion, macro_view

            expansion = macro_expansion(gen, stmt.expr)
            if isinstance(expansion, Block):
                with macro_view(gen, stmt.expr.name):
                    emit_block(gen, builder, expansion.body, dict(scope))
                return

        from siec.codegen.ownership import (begin_temporary_frame,
                                           finish_temporary_frame)

        owns = begin_temporary_frame(gen)
        value = emit_expression(gen, builder, stmt.expr, None, scope)

        from siec.codegen.ownership import (destroyable,
                                           manually_destroyed_local,
                                           set_drop_flag)

        if (name := manually_destroyed_local(stmt.expr)) in scope:
            variable = scope[name]
            if destroyable(gen, variable.type):
                set_drop_flag(builder, variable, False)

        finish_temporary_frame(gen, builder, owns)

        # a statement calling an '@noreturn' function ends its path: the
        # block terminates here, satisfying any required return after it
        if (isinstance(value, ir.CallInstr) and isinstance(value.callee, ir.Function)
                and "noreturn" in value.callee.attributes):
            builder.unreachable()
    else:
        raise TypeError(f"cannot generate code for statement {stmt!r}")


def emit_assignment(gen: CodeGenerator, builder: ir.IRBuilder,
                    stmt, scope: dict) -> None:
    """
    Emit every plain assignment through the shared lvalue abstraction.

    Statement-specific AST shapes are converted back to their expression
    target once; type checks, address resolution, coercion, and volatility
    then follow one path.
    """
    if isinstance(stmt, Assign):
        target = Var(stmt.name, qualified=stmt.qualified)
    elif isinstance(stmt, MemberAssign):
        target = Member(stmt.base, stmt.field)
    elif isinstance(stmt, RefAssign):
        target = stmt.target
    elif isinstance(stmt, IndexAssign):
        target = Index(stmt.base, stmt.index)
    else:
        raise TypeError(f"not an assignment statement: {stmt!r}")

    if isinstance(target, Index):
        target.item_set_call = getattr(stmt, "item_set_call", None)
        target.item_get_call = getattr(stmt, "item_get_call", None)

    # A complete macro place expands before resolution. Members and indices
    # rooted in a macro remain normal lvalue chains; emit_lvalue expands their
    # base at the point its address is needed.
    place = (
        (stmt.macro_name, stmt.macro_target)
        if hasattr(stmt, "macro_target")
        else (None if isinstance(target, Var) and target.qualified
              else macro_place(gen, target, scope))
    )
    if place is not None:
        name, expansion = place
        emit_macro_assignment(
            gen, builder, name, expansion, stmt.value, stmt.line, scope, stmt)
        return

    place = resolve_lvalue(
        gen, builder, target, scope,
        allow_const_init=getattr(stmt, "const_init", False))
    if hasattr(stmt, "assignment_action"):
        initializing = stmt.initialization
        action = stmt.assignment_action
    elif not gen.emitting:
        from siec.codegen.assignment import (
            AssignmentAction,
            assignment_action,
            initializes_uninitialized_member,
        )

        initializing = initializes_uninitialized_member(target, scope)
        action = (
            AssignmentAction(None, stmt.value)
            if initializing
            else assignment_action(
                gen, target, place.type, stmt.value, scope)
        )
    else:
        raise RuntimeError("assignment reached Emit without a checked action")
    if action.call is not None:
        emit_expression(gen, builder, action.call, None, scope)
        return

    from siec.codegen.ownership import (DropCleanup, consume_temporary,
                                       destroyable,
                                       disarm_expression,
                                       emit_drop_cleanup, emit_drop_slot,
                                       set_drop_flag)

    if (not initializing and isinstance(place, AddressLValue)
            and destroyable(gen, place.type)):
        # Preserve ordinary assignment evaluation order: retain the target
        # address, compute the replacement completely, then release the old
        # value before storing the new owner.
        address = place.address()
        emitted = emit_coerced(
            gen, builder, action.value, place.type, place.scope)
        consume_temporary(gen, action.value)
        disarm_expression(gen, builder, action.value, scope)

        variable = (scope.get(target.name)
                    if isinstance(target, Var) else None)
        if variable is not None and variable.drop_flag is not None:
            emit_drop_cleanup(
                gen, builder, DropCleanup(target.name, variable))
        else:
            emit_drop_slot(gen, builder, address, place.type)

        store = builder.store(emitted, address)
        volatile_store(gen, store)
        if variable is not None:
            set_drop_flag(builder, variable, True)
        return

    place.store(action.value)

    consume_temporary(gen, action.value)
    disarm_expression(gen, builder, action.value, scope)
    if isinstance(target, Var) and target.name in scope:
        set_drop_flag(builder, scope[target.name], True)


def emit_compound_assign(gen: CodeGenerator, builder: ir.IRBuilder,
                         stmt, scope: dict) -> None:
    """
    Emit 'lvalue <op>= value'.

    A type that updates in place takes it directly: 'dec += 1' is
    'dec.add_assign(1)', which spends no copy and leaves nothing to
    reassign. Without that method the operator's result assigns back,
    'dec = dec + 1', the way every numeric target works.
    """
    macro = (
        (stmt.macro_name, stmt.macro_target)
        if hasattr(stmt, "macro_target")
        else macro_place(gen, stmt.target, scope)
    )
    if macro is not None:
        name, expansion = macro
        expanded = CompoundAssign(
            expansion, stmt.op, stmt.value, line=stmt.line)
        for attr in (
                "compound_call", "compound_action", "item_get_call",
                "item_set_call"):
            if hasattr(stmt, attr):
                setattr(expanded, attr, getattr(stmt, attr))
        with macro_view(gen, name):
            emit_compound_assign(
                gen, builder, expanded, scope)
        return

    def bind_operator_rewrite(replacement, checked_value) -> None:
        """Transfer checked plans to a cached assignment replacement."""
        from dataclasses import replace
        from siec.codegen.hir import (
            BinaryPlan,
            checked_binary,
            checked_call,
            checked_coercion,
            stamp,
        )

        stamp(
            replacement,
            sie_type=getattr(checked_value, "sie_type", None),
            expected_type=getattr(checked_value, "expected_type", None),
            coercion_plan=checked_coercion(checked_value),
            overwrite=True,
        )

        checked_plan = checked_binary(checked_value)
        if checked_plan is not None and isinstance(checked_value, BinaryOp):
            stamp(
                replacement.left,
                sie_type=getattr(checked_value.left, "sie_type", None),
                expected_type=getattr(
                    checked_value.left, "expected_type", None),
                coercion_plan=checked_coercion(checked_value.left),
                overwrite=True,
            )
            if checked_plan.kind != "rewrite":
                pointer = checked_plan.pointer
                index = checked_plan.index
                if pointer is checked_value.left:
                    pointer = replacement.left
                elif pointer is checked_value.right:
                    pointer = replacement.right
                if index is checked_value.left:
                    index = replacement.left
                elif index is checked_value.right:
                    index = replacement.right
                stamp(
                    replacement,
                    binary_plan=replace(
                        checked_plan, pointer=pointer, index=index),
                    overwrite=True,
                )

        checked_rewrite = getattr(checked_value, "operator_rewrite", None)
        if not isinstance(checked_rewrite, MethodCall):
            return
        stamp(
            replacement.left,
            sie_type=getattr(checked_rewrite.receiver, "sie_type", None),
            expected_type=getattr(
                checked_rewrite.receiver, "expected_type", None),
            coercion_plan=checked_coercion(checked_rewrite.receiver),
            overwrite=True,
        )
        rewritten = MethodCall(
            replacement.left,
            checked_rewrite.method,
            [replacement.right],
            checked_rewrite.type_args,
        )
        plan = checked_call(checked_rewrite)
        if plan.receiver is not None:
            plan = replace(plan, receiver=replacement.left)
        stamp(
            rewritten,
            resolved_symbol=checked_rewrite.resolved_symbol,
            call_plan=plan,
            overwrite=True,
        )
        replacement.operator_rewrite = rewritten
        stamp(
            replacement,
            binary_plan=BinaryPlan(
                "rewrite",
                getattr(checked_value, "sie_type", None),
                replacement=rewritten,
            ),
            overwrite=True,
        )

    if isinstance(stmt.target, Index):
        stmt.target.item_get_call = getattr(stmt, "item_get_call", None)
        stmt.target.item_set_call = getattr(stmt, "item_set_call", None)

    place = resolve_lvalue(
        gen, builder, stmt.target, scope, item_mode="update")
    # a struct's indexed getter returns a value, not its storage: update
    # that value through the binary operator, then hand it back to the
    # indexed setter. Do this before considering V's own in-place method,
    # which would otherwise try to mutate the temporary get_item returned.
    if isinstance(place, ItemLValue):
        place.stabilize()
        replacement = BinaryOp(stmt.op, place.cached_load(), stmt.value)
        bind_operator_rewrite(replacement, stmt.compound_action.value)
        place.store(replacement)
        return

    if stmt.compound_call is not None:
        emit_expression(gen, builder, stmt.compound_call, None, scope)
        return

    if place.type is None:
        raise TypeError("cannot determine the type of compound assignment "
                        "target")

    # The address is emitted by cached_load() and retained for store(), so a
    # complex target is evaluated exactly once without a synthetic scope name.
    replacement = BinaryOp(stmt.op, place.cached_load(), stmt.value)
    checked_action = stmt.compound_action
    checked_value = (
        checked_action.call.args[0]
        if checked_action.call is not None
        else checked_action.value
    )
    bind_operator_rewrite(replacement, checked_value)
    if checked_action.call is not None:
        from dataclasses import replace
        from siec.codegen.hir import checked_call, stamp

        call = MethodCall(
            place.target, checked_action.call.method, [replacement])
        plan = checked_call(checked_action.call)
        if plan.receiver is not None:
            plan = replace(plan, receiver=place.target)
        stamp(
            call,
            resolved_symbol=checked_action.call.resolved_symbol,
            call_plan=plan,
            overwrite=True,
        )
        emit_expression(gen, builder, call, None, scope)
        return

    place.store(replacement)


def emit_sized_array_let(gen: CodeGenerator, builder: ir.IRBuilder, stmt: Let,
                         sized: tuple[str, str], scope: dict) -> None:
    """
    Declare a sized array 'let a: X[N];': an 'X[]' whose data points at N
    automatically allocated stack elements and whose length starts at N.
    """
    if stmt.value is not None:
        raise TypeError(f"a sized array takes its contents from its size; "
                        f"initialize an {sized[0]!r} instead")

    sie_type, size = sized[0], evaluate_size(gen, sized[1])
    var_type = resolve_type(sie_type, gen.structs)

    backing = entry_alloca(builder, ir.ArrayType(var_type.elements[0].pointee, size),
                           f"{stmt.name}.backing")
    data = builder.gep(backing, [ir.Constant(ir.IntType(32), 0),
                                 ir.Constant(ir.IntType(32), 0)], name=f"{stmt.name}.data")

    value = ir.Constant(var_type, ir.Undefined)
    value = builder.insert_value(value, data, 0)
    value = builder.insert_value(value, ir.Constant(ir.IntType(64), size), 1)

    from siec.codegen.ownership import (DropCleanup, destroyable,
                                       new_drop_flag)

    owned = destroyable(gen, sie_type)
    drop_flag = new_drop_flag(builder, stmt.name, owned) if owned else None
    scope[stmt.name] = Variable(
        entry_alloca(builder, var_type, stmt.name), sie_type,
        drop_flag=drop_flag)
    builder.store(value, scope[stmt.name].slot)
    if owned:
        gen.defer_frames[-1].append(
            DropCleanup(stmt.name, scope[stmt.name]))

    if gen.debug is not None:
        gen.debug.declare_variable(builder, scope[stmt.name].slot,
                                   stmt.name, sie_type, stmt.line)


def emit_while(gen: CodeGenerator, builder: ir.IRBuilder, stmt: While, scope: dict) -> None:
    """
    Emit a while loop: the condition checked before each pass, C-style.
    """
    func = builder.function
    cond_block = func.append_basic_block("while.cond")
    body_block = func.append_basic_block("while.body")
    end_block = func.append_basic_block("while.end")

    builder.branch(cond_block)

    # compare non-boolean conditions against zero, like an if's
    builder.position_at_end(cond_block)
    condition = emit_full_expression(
        gen, builder, stmt.condition, scope, boolean=True)
    builder.cbranch(condition, body_block, end_block)

    # the body runs in a child scope of its own, fresh each iteration,
    # and loops back to the condition unless it returned
    builder.position_at_end(body_block)
    gen.loop_targets.append((end_block, cond_block, len(gen.defer_frames)))
    emit_block(gen, builder, stmt.body, dict(scope))
    gen.loop_targets.pop()

    if not builder.block.is_terminated:
        builder.branch(cond_block)

    builder.position_at_end(end_block)


def bind_tuple_value(gen: CodeGenerator, builder: ir.IRBuilder, pattern: list,
                     pattern_types: list, value, scope: dict,
                     line: int | None = None) -> None:
    """
    Bind a checked tuple pattern from an already-emitted tuple value.

    Each name gets a fresh local holding its element copy. ``pattern_types``
    is the resolved tree recorded during semantic checking.
    """
    for i, (sub, element_type) in enumerate(zip(pattern, pattern_types)):
        element = builder.extract_value(value, i)
        if isinstance(sub, list):
            bind_tuple_value(
                gen, builder, sub, element_type, element, scope, line=line)
            continue

        slot = entry_alloca(builder, element.type, sub)
        builder.store(element, slot)
        scope[sub] = Variable(slot, element_type)

        if gen.debug is not None and line is not None:
            gen.debug.declare_variable(
                builder, slot, sub, element_type, line)


def emit_let_tuple(gen: CodeGenerator, builder: ir.IRBuilder, stmt: LetTuple,
                   scope: dict) -> None:
    """
    Emit 'let (a, b) = pair;': the tuple emits once and each name binds
    a fresh local holding its element - a copy, the binder's own, like
    any scalar copy. Nested patterns recurse into nested tuples.
    """
    from siec.codegen.expressions import emit_expression

    value = emit_expression(gen, builder, stmt.value, None, scope)
    bind_tuple_value(
        gen, builder, stmt.pattern, stmt.pattern_types, value, scope,
        line=stmt.line)


def emit_foreach(gen: CodeGenerator, builder: ir.IRBuilder, stmt: Foreach,
                 scope: dict) -> None:
    """
    Emit 'foreach (v : iterable)': the iterable hands out its iterator
    ('iterator()', or itself when it already is one), and each pass binds
    'v' to the address 'next()' returns - a true reference into the
    collection, not a copy, exactly like a reference parameter.
    """
    from siec.ast import Var
    from siec.codegen.calls import emit_call
    from siec.codegen.types import resolve_type

    loop_scope = dict(scope)
    it_name = "__foreach_it"

    # an Iterable hands out its iterator - a const source its
    # const_iterator; a value that already is an iterator iterates
    # itself, from a copy of its state
    it_type = stmt.iterator_type
    if stmt.iterator_call is not None:
        it_value = emit_expression(
            gen, builder, stmt.iterator_call, None, scope)
    else:
        it_value = emit_expression(gen, builder, stmt.iterable, None, scope)

    slot = entry_alloca(builder, resolve_type(it_type, gen.structs), "foreach.it")
    if (align := gen.struct_align(it_type)) is not None:
        slot.align = align

    builder.store(it_value, slot)
    loop_scope[it_name] = Variable(slot, it_type)

    next_symbol = stmt.next_call.resolved_symbol
    next_ret = gen.return_types[next_symbol]

    func = builder.function
    cond_block = func.append_basic_block("foreach.cond")
    body_block = func.append_basic_block("foreach.body")
    end_block = func.append_basic_block("foreach.end")

    builder.branch(cond_block)

    builder.position_at_end(cond_block)
    builder.cbranch(emit_bool(
                        gen, builder, stmt.has_next_call, loop_scope),
                    body_block, end_block)

    # each pass takes the next element's address and binds 'v' to it,
    # the way a reference parameter binds its caller's storage
    builder.position_at_end(body_block)
    address = emit_call(
        gen, builder, stmt.next_call, loop_scope, as_address=True)

    body_scope = dict(loop_scope)
    body_scope[stmt.name] = Variable(address, next_ret)

    gen.loop_targets.append((end_block, cond_block, len(gen.defer_frames)))
    emit_block(gen, builder, stmt.body, body_scope)
    gen.loop_targets.pop()

    if not builder.block.is_terminated:
        builder.branch(cond_block)

    builder.position_at_end(end_block)


def emit_for(gen: CodeGenerator, builder: ir.IRBuilder, stmt: For, scope: dict) -> None:
    """
    Emit a for loop: the init once, the condition before each pass, and the
    step after each.
    """
    # the loop is its own scope; the init's variable lives exactly as long as it
    loop_scope = dict(scope)
    emit_statement(gen, builder, stmt.init, loop_scope)

    func = builder.function
    cond_block = func.append_basic_block("for.cond")
    body_block = func.append_basic_block("for.body")
    step_block = func.append_basic_block("for.step")
    end_block = func.append_basic_block("for.end")

    builder.branch(cond_block)

    builder.position_at_end(cond_block)
    condition = emit_full_expression(
        gen, builder, stmt.condition, loop_scope, boolean=True)
    builder.cbranch(condition, body_block, end_block)

    # the body runs in a child scope, fresh each iteration; the step follows
    # in a block of its own, where a 'continue' lands, then control returns
    # to the condition
    builder.position_at_end(body_block)
    gen.loop_targets.append((end_block, step_block, len(gen.defer_frames)))
    emit_block(gen, builder, stmt.body, dict(loop_scope))
    gen.loop_targets.pop()

    if not builder.block.is_terminated:
        builder.branch(step_block)

    builder.position_at_end(step_block)
    emit_statement(gen, builder, stmt.step, loop_scope)
    builder.branch(cond_block)

    builder.position_at_end(end_block)


def expand_when_interface(gen: CodeGenerator, arm: When, scope: dict) -> list:
    """
    A 'when Iface:' arm of a '@typeof' case is a generic arm: it expands
    into one arm per type known to implement the interface, each body
    stamped with the concrete type wherever the arm's spelling appears,
    so 'args[i] as Formattable' reads 'args[i] as i64' in the 'i64' arm.
    A nested interface argument expands per combination:
    'Iterable<Formattable>' arms every iterable of every formattable.
    """
    import copy

    from siec.codegen.generics import respell_types
    from siec.codegen.interfaces import interface_expansions

    plain, spellings = [], []
    for value in arm.values:
        if (isinstance(value, Var) and value.name not in scope
                and value.name in gen.interfaces):
            spelling = value.name
            if value.type_args is not None:
                spelling += f"<{','.join(value.type_args)}>"
            spellings.append(spelling)
        else:
            plain.append(value)

    if not spellings:
        return [arm]

    arms = [When(plain, arm.body)] if plain else []
    for spelling in spellings:
        for concrete in interface_expansions(gen, spelling):
            body = copy.deepcopy(arm.body)
            respell_types(body, spelling, concrete)
            expanded = When([TypeId(concrete)], body)
            expanded.runtime_interface_type = concrete
            arms.append(expanded)

    return arms


def emit_case(gen: CodeGenerator, builder: ir.IRBuilder, stmt: Case, scope: dict) -> None:
    """
    Emit a case as a chain of equality tests: the subject is evaluated
    once, the first matching arm runs in a scope of its own, and control
    jumps past the case, with no fall-through.
    """
    # matching on '@typeof' lets bare type names arm the case:
    # 'when T:' means 'when @typeid(T):', and 'when Iface:' expands
    # into an arm per implementing type
    if isinstance(stmt.subject, TypeOf):
        from siec.codegen.expressions import type_operand
        from siec.codegen.inference import infer_type
        from siec.codegen.types import strip_const, strip_reference

        stmt.arms = [expanded for arm in stmt.arms
                     for expanded in expand_when_interface(gen, arm, scope)]

        subject_type = infer_type(gen, stmt.subject.value, scope)
        if (strip_const(strip_reference(subject_type or "")) == "Any"
                and gen.live_any_types is not None):
            stmt.arms = [
                arm for arm in stmt.arms
                if getattr(arm, "runtime_interface_type", None) is None
                or arm.runtime_interface_type in gen.live_any_types
            ]

        for arm in stmt.arms:
            arm.values = [type_operand(gen, value, scope)
                          for value in arm.values]

    subject = emit_full_expression(gen, builder, stmt.subject, scope)
    if not isinstance(subject.type, (ir.IntType, ir.PointerType,
                                     ir.FloatType, ir.DoubleType)):
        raise TypeError(f"cannot match on a value of type {subject.type}")

    func = builder.function
    end_block = func.append_basic_block("case.end")
    falls = False

    for arm in stmt.arms:
        # any of the arm's values selects it; each adopts the subject's
        # type, like a comparison's right side
        cond = None
        for value_expr in arm.values:
            value = emit_full_expression(
                gen, builder, value_expr, scope, expected=subject.type)
            if isinstance(subject.type, (ir.FloatType, ir.DoubleType)):
                test = builder.fcmp_ordered("==", subject, value)
            else:
                test = builder.icmp_unsigned("==", subject, value)

            cond = test if cond is None else builder.or_(cond, test)

        body_block = func.append_basic_block("when.body")
        next_block = func.append_basic_block("when.next")
        builder.cbranch(cond, body_block, next_block)

        builder.position_at_end(body_block)
        emit_block(gen, builder, arm.body, dict(scope))

        if not builder.block.is_terminated:
            falls = True
            builder.branch(end_block)

        builder.position_at_end(next_block)

    # no arm matched: the else body when given, nothing otherwise
    if stmt.orelse is not None:
        emit_block(gen, builder, stmt.orelse, dict(scope))

    if not builder.block.is_terminated:
        falls = True
        builder.branch(end_block)

    # when every path returns, the end block exists only to hold 'unreachable'
    builder.position_at_end(end_block)
    if not falls:
        builder.unreachable()


def emit_if(gen: CodeGenerator, builder: ir.IRBuilder, stmt: If, scope: dict) -> None:
    """
    Emit an if/else as a conditional branch over new basic blocks.
    """
    # compare non-boolean conditions against zero, C-style
    cond = emit_full_expression(
        gen, builder, stmt.condition, scope, boolean=True)

    func = builder.function
    then_block = func.append_basic_block("if.then")
    else_block = func.append_basic_block("if.else") if stmt.orelse else None
    end_block = func.append_basic_block("if.end")

    builder.cbranch(cond, then_block, else_block or end_block)

    # each arm falls through to the end block unless it already returned,
    # and runs in a child scope of its own, like any block
    builder.position_at_end(then_block)
    emit_block(gen, builder, stmt.body, dict(scope))

    then_falls = not builder.block.is_terminated
    if then_falls:
        builder.branch(end_block)

    else_falls = else_block is None
    if else_block is not None:
        builder.position_at_end(else_block)
        emit_block(gen, builder, stmt.orelse, dict(scope))
    
        else_falls = not builder.block.is_terminated
        if else_falls:
            builder.branch(end_block)

    # when neither branch falls through, the end block exists only to hold 'unreachable'
    builder.position_at_end(end_block)
    if not (then_falls or else_falls):
        builder.unreachable()
