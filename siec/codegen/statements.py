"""Emission of statements and control flow."""

from llvmlite import ir

from ..ast import Assign, ExprStmt, If, Let, Member, MemberAssign, Return
from .errors import source_location
from .expressions import emit_bool, emit_coerced, emit_expression, emit_lvalue, member_field
from .generator import CodeGenerator, Variable
from .types import resolve_type


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
        # reserve a stack slot for the variable and initialize it if a value was given,
        # widening the initializer to the declared type when allowed
        var_type = resolve_type(stmt.type, gen.structs)
        scope[stmt.name] = Variable(builder.alloca(var_type, name=stmt.name), stmt.type)

        if stmt.value is not None:
            builder.store(emit_coerced(gen, builder, stmt.value, stmt.type, scope),
                          scope[stmt.name].slot)
    elif isinstance(stmt, Assign):
        # store the value into the variable's existing stack slot, typed by the slot
        if stmt.name not in scope:
            raise NameError(f"undefined variable {stmt.name!r}")

        var = scope[stmt.name]
        builder.store(emit_coerced(gen, builder, stmt.value, var.type, scope), var.slot)
    elif isinstance(stmt, MemberAssign):
        # store the value into the field's slot, typed by the field
        member = Member(stmt.base, stmt.field)
        field_type = member_field(gen, member, scope)[1]
        slot = emit_lvalue(gen, builder, member, scope)
        builder.store(emit_coerced(gen, builder, stmt.value, field_type, scope), slot)
    elif isinstance(stmt, If):
        emit_if(gen, builder, stmt, scope)
    elif isinstance(stmt, Return):
        if stmt.value is None:
            builder.ret_void()
        else:
            ret_type = gen.return_types[builder.function.name]
            builder.ret(emit_coerced(gen, builder, stmt.value, ret_type, scope))
    elif isinstance(stmt, ExprStmt):
        emit_expression(gen, builder, stmt.expr, None, scope)
    else:
        raise TypeError(f"cannot generate code for statement {stmt!r}")


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

    # each branch falls through to the end block unless it already returned
    builder.position_at_end(then_block)
    emit_block(gen, builder, stmt.body, scope)

    then_falls = not builder.block.is_terminated
    if then_falls:
        builder.branch(end_block)

    else_falls = else_block is None
    if else_block is not None:
        builder.position_at_end(else_block)
        emit_block(gen, builder, stmt.orelse, scope)
    
        else_falls = not builder.block.is_terminated
        if else_falls:
            builder.branch(end_block)

    # when neither branch falls through, the end block exists only to hold 'unreachable'
    builder.position_at_end(end_block)
    if not (then_falls or else_falls):
        builder.unreachable()
