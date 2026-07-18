"""Emission of statements and control flow."""

from llvmlite import ir

from siec.ast import (
    Assign,
    Block,
    Case,
    Defer,
    Emit,
    ExprStmt,
    For,
    If,
    Index,
    IndexAssign,
    Let,
    Member,
    MemberAssign,
    Return,
    While,
)
from siec.codegen.coercion import emit_coerced
from siec.codegen.enums import evaluate_size
from siec.codegen.errors import source_location
from siec.codegen.expressions import emit_bool, emit_expression, emit_lvalue
from siec.codegen.inference import expr_sie_type, infer_type, member_field
from siec.codegen.generator import CodeGenerator, Variable, entry_alloca, make_volatile
from siec.codegen.types import (
    is_const,
    is_reference,
    resolve_type,
    sized_array,
    strip_const,
    strip_reference,
)


def emit_block(gen: CodeGenerator, builder: ir.IRBuilder, stmts: list, scope: dict) -> None:
    """
    Emit statements in order, stopping once the current block is terminated.

    Each block is a defer scope: statements deferred inside it run when it
    falls off its end; a 'return' or 'emit' leaving it early flushes them
    itself, along the exiting path.
    """
    gen.defer_frames.append([])

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

    The frames stay in place — other paths out of the same scopes flush
    their own copies.
    """
    gen.flushing_defers += 1
    try:
        for frame in reversed(frames):
            for stmt, snapshot in reversed(frame):
                emit_statement(gen, builder, stmt, snapshot)
    finally:
        gen.flushing_defers -= 1


def volatile_chain(gen: CodeGenerator, expr, scope: dict) -> bool:
    """
    Whether an lvalue chain passes through a '@volatile' struct: any link
    whose type names one, directly or behind pointers and arrays.
    """
    node = expr
    while True:
        name = strip_const(expr_sie_type(gen, node, scope)) or ""
        while name.endswith("*") or name.endswith("[]"):
            name = name.removesuffix("[]").rstrip("*")

        info = gen.structs.get(name)
        if info is not None and info.volatile:
            return True

        if isinstance(node, (Member, Index)):
            node = node.base
        else:
            return False


def volatile_store(gen: CodeGenerator, store) -> None:
    """
    Mark a store volatile when it writes a '@volatile' struct value.
    """
    if gen.volatile_struct(store.operands[0].type):
        make_volatile(store)


def reject_const_base(gen: CodeGenerator, scope: dict, base) -> None:
    """
    Reject assignment through anything 'const': every link of the target's
    base chain must be mutable, since the contract follows the value.
    """
    while True:
        base_type = expr_sie_type(gen, base, scope)
        if is_const(base_type):
            raise TypeError(f"cannot mutate a {base_type!r} value")

        if isinstance(base, (Member, Index)):
            base = base.base
        else:
            return


def emit_statement(gen: CodeGenerator, builder: ir.IRBuilder, stmt, scope: dict) -> None:
    """
    Emit a single statement into the builder's current block, tagging errors with its line.
    """
    with source_location(line=getattr(stmt, "line", 0)):
        emit_statement_body(gen, builder, stmt, scope)


def emit_statement_body(gen: CodeGenerator, builder: ir.IRBuilder, stmt, scope: dict) -> None:
    """
    Emit a single statement into the builder's current block.
    """
    if isinstance(stmt, Let):
        # an unannotated 'let' takes its type from its initializer
        type_name = stmt.type
        if type_name is None:
            type_name = infer_type(gen, stmt.value, scope)
            if type_name is None:
                raise TypeError(f"cannot infer a type for {stmt.name!r}: "
                                "annotate it explicitly")

        # references only pass parameters; a variable is its own storage
        if is_reference(type_name):
            raise TypeError("a reference cannot type a variable")

        # a sized array 'X[N]' declares an 'X[]' backed by N stack elements
        if (sized := sized_array(type_name)) is not None:
            emit_sized_array_let(gen, builder, stmt, sized, scope)
            return

        # reserve a stack slot for the variable and initialize it if a value was given,
        # widening the initializer to the declared type when allowed
        var_type = resolve_type(type_name, gen.structs)
        slot = entry_alloca(builder, var_type, stmt.name)

        # an '@align(N)' struct's slot honors the declared alignment
        if (align := gen.struct_align(type_name)) is not None:
            slot.align = align

        scope[stmt.name] = Variable(slot, type_name)

        if stmt.value is not None:
            volatile_store(gen, builder.store(
                emit_coerced(gen, builder, stmt.value, type_name, scope), slot))
    elif isinstance(stmt, Assign):
        # store the value into the variable's existing stack slot, typed by
        # the slot; a global's slot is its module-level storage
        if stmt.name in scope:
            var = scope[stmt.name]
            slot, var_type = var.slot, var.type
        elif (symbol := gen.resolve_symbol(stmt.name)) in gen.globals:
            slot, var_type = gen.module.globals[symbol], gen.globals[symbol]
        elif stmt.name in gen.constants:
            raise TypeError(f"cannot reassign constant {stmt.name!r}")
        else:
            raise NameError(f"undefined variable {stmt.name!r}")

        if is_const(var_type):
            raise TypeError(f"cannot assign to const variable {stmt.name!r}")

        # assigning to a '&T' reference writes the T it aliases
        volatile_store(gen, builder.store(emit_coerced(
            gen, builder, stmt.value, strip_reference(var_type), scope), slot))
    elif isinstance(stmt, MemberAssign):
        # store the value into the field's slot, typed by the field; a
        # write into a '@volatile' struct is a volatile one
        member = Member(stmt.base, stmt.field)
        field_type = member_field(gen, member, scope)[1]
        if is_const(field_type):
            raise TypeError(f"cannot assign to const field {stmt.field!r}")

        reject_const_base(gen, scope, stmt.base)
        slot = emit_lvalue(gen, builder, member, scope)
        store = builder.store(emit_coerced(gen, builder, stmt.value, field_type, scope), slot)
        if volatile_chain(gen, member, scope):
            make_volatile(store)
    elif isinstance(stmt, IndexAssign):
        # store the value into the element's slot, typed by the element; a
        # write into a '@volatile' struct is a volatile one
        reject_const_base(gen, scope, stmt.base)
        target = Index(stmt.base, stmt.index)
        slot = emit_lvalue(gen, builder, target, scope)

        element_type = expr_sie_type(gen, target, scope)
        if element_type is not None:
            value = emit_coerced(gen, builder, stmt.value, element_type, scope)
        else:
            value = emit_expression(gen, builder, stmt.value, slot.type.pointee, scope)

        store = builder.store(value, slot)
        if volatile_chain(gen, target, scope):
            make_volatile(store)
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
    elif isinstance(stmt, Defer):
        # capture the statement with the scope as it stands: the shared
        # slots make later writes visible when it finally runs, while
        # later shadowing declarations stay out of sight
        gen.defer_frames[-1].append((stmt.stmt, dict(scope)))
    elif isinstance(stmt, Emit):
        # store the value into the enclosing block expression's slot and
        # jump past the block, ending it early like a return ends a function
        if gen.flushing_defers:
            raise TypeError("a deferred statement cannot emit")

        if not gen.emit_targets:
            raise TypeError("'emit' outside a block expression")

        slot, end_block, target_name, depth = gen.emit_targets[-1]
        if target_name is not None:
            value = emit_coerced(gen, builder, stmt.value, target_name, scope)
        else:
            value = emit_expression(gen, builder, stmt.value, slot.type.pointee, scope)

        # the value is computed before the scopes being left run their defers
        builder.store(value, slot)
        flush_defers(gen, builder, gen.defer_frames[depth:])
        builder.branch(end_block)
    elif isinstance(stmt, Return):
        # a deferred statement runs on the way out of a scope; returning
        # there would flush the very frame holding it
        if gen.flushing_defers:
            raise TypeError("a deferred statement cannot return")

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
            # the return value is computed before any deferred statement runs
            ret_type = gen.return_types[builder.function.name]
            value = emit_coerced(gen, builder, stmt.value, ret_type, scope)
            flush_defers(gen, builder, gen.defer_frames)
            builder.ret(value)
    elif isinstance(stmt, ExprStmt):
        emit_expression(gen, builder, stmt.expr, None, scope)
    else:
        raise TypeError(f"cannot generate code for statement {stmt!r}")


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

    scope[stmt.name] = Variable(entry_alloca(builder, var_type, stmt.name), sie_type)
    builder.store(value, scope[stmt.name].slot)


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
    builder.cbranch(emit_bool(gen, builder, stmt.condition, scope),
                    body_block, end_block)

    # the body runs in a child scope of its own, fresh each iteration,
    # and loops back to the condition unless it returned
    builder.position_at_end(body_block)
    emit_block(gen, builder, stmt.body, dict(scope))

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
    end_block = func.append_basic_block("for.end")

    builder.branch(cond_block)

    builder.position_at_end(cond_block)
    builder.cbranch(emit_bool(gen, builder, stmt.condition, loop_scope),
                    body_block, end_block)

    # the body runs in a child scope, fresh each iteration; the step follows
    # it in the loop's own scope, then control returns to the condition
    builder.position_at_end(body_block)
    emit_block(gen, builder, stmt.body, dict(loop_scope))

    if not builder.block.is_terminated:
        emit_statement(gen, builder, stmt.step, loop_scope)
        builder.branch(cond_block)

    builder.position_at_end(end_block)


def emit_case(gen: CodeGenerator, builder: ir.IRBuilder, stmt: Case, scope: dict) -> None:
    """
    Emit a case as a chain of equality tests: the subject is evaluated
    once, the first matching arm runs in a scope of its own, and control
    jumps past the case, with no fall-through.
    """
    subject = emit_expression(gen, builder, stmt.subject, None, scope)
    if not isinstance(subject.type, (ir.IntType, ir.PointerType,
                                     ir.FloatType, ir.DoubleType)):
        raise TypeError(f"cannot match on a value of type {subject.type}")

    func = builder.function
    end_block = func.append_basic_block("case.end")
    falls = False

    for arm in stmt.arms:
        # each value adopts the subject's type, like a comparison's right side
        value = emit_expression(gen, builder, arm.value, subject.type, scope)
        if isinstance(subject.type, (ir.FloatType, ir.DoubleType)):
            cond = builder.fcmp_ordered("==", subject, value)
        else:
            cond = builder.icmp_unsigned("==", subject, value)

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
    cond = emit_bool(gen, builder, stmt.condition, scope)

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
