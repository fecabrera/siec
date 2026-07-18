"""Emission of statements and control flow."""

from llvmlite import ir

from siec.ast import (
    Assign,
    Block,
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
from siec.codegen.errors import source_location
from siec.codegen.expressions import (
    emit_bool,
    emit_coerced,
    emit_expression,
    emit_lvalue,
    expr_sie_type,
    member_field,
)
from siec.codegen.generator import CodeGenerator, Variable, entry_alloca
from siec.codegen.types import resolve_type, sized_array


def emit_block(gen: CodeGenerator, builder: ir.IRBuilder, stmts: list, scope: dict) -> None:
    """
    Emit statements in order, stopping once the current block is terminated.
    """
    for stmt in stmts:
        emit_statement(gen, builder, stmt, scope)
        if builder.block.is_terminated:
            break


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
        # a sized array 'X[N]' declares an 'X[]' backed by N stack elements
        if (sized := sized_array(stmt.type)) is not None:
            emit_sized_array_let(gen, builder, stmt, sized, scope)
            return

        # reserve a stack slot for the variable and initialize it if a value was given,
        # widening the initializer to the declared type when allowed
        var_type = resolve_type(stmt.type, gen.structs)
        scope[stmt.name] = Variable(entry_alloca(builder, var_type, stmt.name), stmt.type)

        if stmt.value is not None:
            builder.store(emit_coerced(gen, builder, stmt.value, stmt.type, scope),
                          scope[stmt.name].slot)
    elif isinstance(stmt, Assign):
        # store the value into the variable's existing stack slot, typed by the slot
        if stmt.name not in scope:
            if stmt.name in gen.constants:
                raise TypeError(f"cannot reassign constant {stmt.name!r}")

            raise NameError(f"undefined variable {stmt.name!r}")

        var = scope[stmt.name]
        builder.store(emit_coerced(gen, builder, stmt.value, var.type, scope), var.slot)
    elif isinstance(stmt, MemberAssign):
        # store the value into the field's slot, typed by the field
        member = Member(stmt.base, stmt.field)
        field_type = member_field(gen, member, scope)[1]
        slot = emit_lvalue(gen, builder, member, scope)
        builder.store(emit_coerced(gen, builder, stmt.value, field_type, scope), slot)
    elif isinstance(stmt, IndexAssign):
        # store the value into the element's slot, typed by the element
        target = Index(stmt.base, stmt.index)
        slot = emit_lvalue(gen, builder, target, scope)

        element_type = expr_sie_type(gen, target, scope)
        if element_type is not None:
            value = emit_coerced(gen, builder, stmt.value, element_type, scope)
        else:
            value = emit_expression(gen, builder, stmt.value, slot.type.pointee, scope)

        builder.store(value, slot)
    elif isinstance(stmt, Block):
        # a block runs in a child scope: writes to outer variables persist
        # through their shared slots, while inner declarations end with it
        emit_block(gen, builder, stmt.body, dict(scope))
    elif isinstance(stmt, If):
        emit_if(gen, builder, stmt, scope)
    elif isinstance(stmt, While):
        emit_while(gen, builder, stmt, scope)
    elif isinstance(stmt, For):
        emit_for(gen, builder, stmt, scope)
    elif isinstance(stmt, Emit):
        # store the value into the enclosing block expression's slot and
        # jump past the block, ending it early like a return ends a function
        if not gen.emit_targets:
            raise TypeError("'emit' outside a block expression")

        slot, end_block, target_name = gen.emit_targets[-1]
        if target_name is not None:
            value = emit_coerced(gen, builder, stmt.value, target_name, scope)
        else:
            value = emit_expression(gen, builder, stmt.value, slot.type.pointee, scope)

        builder.store(value, slot)
        builder.branch(end_block)
    elif isinstance(stmt, Return):
        if stmt.value is None:
            # a bare 'return' in main yields its implicit exit code 0: only
            # main is declared without a return type yet lowered to i32
            ret_type = builder.function.function_type.return_type
            if (not isinstance(ret_type, ir.VoidType)
                    and gen.return_types.get(builder.function.name) is None):
                builder.ret(ir.Constant(ret_type, 0))
            else:
                builder.ret_void()
        else:
            ret_type = gen.return_types[builder.function.name]
            builder.ret(emit_coerced(gen, builder, stmt.value, ret_type, scope))
    elif isinstance(stmt, ExprStmt):
        emit_expression(gen, builder, stmt.expr, None, scope)
    else:
        raise TypeError(f"cannot generate code for statement {stmt!r}")


def emit_sized_array_let(gen: CodeGenerator, builder: ir.IRBuilder, stmt: Let,
                         sized: tuple[str, int], scope: dict) -> None:
    """
    Declare a sized array 'let a: X[N];': an 'X[]' whose data points at N
    automatically allocated stack elements and whose length starts at N.
    """
    if stmt.value is not None:
        raise TypeError(f"a sized array takes its contents from its size; "
                        f"initialize an {sized[0]!r} instead")

    sie_type, size = sized
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
