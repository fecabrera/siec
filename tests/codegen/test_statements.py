"""Tests for siec.codegen.statements."""

import pytest
from llvmlite import ir

from siec.ast import Assign, ExprStmt, If, IndexAssign, IntLiteral, Let, Return, Var
from siec.codegen.generator import Variable
from siec.codegen.statements import emit_block, emit_if, emit_statement


def test_let_reserves_a_stack_slot(env):
    """
    A let declaration allocas a slot of the declared type into the scope.
    """
    gen, builder = env
    scope = {}
    emit_statement(gen, builder, Let("num", "i32", None), scope)
    assert scope["num"].slot.opname == "alloca"
    assert scope["num"].slot.type.pointee == ir.IntType(32)


def test_let_initializer_is_stored(env):
    """
    A let initializer is stored into the new slot with the declared type.
    """
    gen, builder = env
    scope = {}
    emit_statement(gen, builder, Let("num", "i32", IntLiteral(4)), scope)
    assert "store i32 4" in str(builder.function)


def test_assign_stores_into_the_slot(env):
    """
    An assignment stores into the existing slot, typed by the slot.
    """
    gen, builder = env
    scope = {}
    emit_statement(gen, builder, Let("num", "i64", None), scope)
    emit_statement(gen, builder, Assign("num", IntLiteral(9)), scope)
    assert "store i64 9" in str(builder.function)


def test_index_assign_stores_through_the_pointer(env):
    """
    An index assignment geps the base pointer and stores the element's type.
    """
    gen, builder = env
    scope = {"p": Variable(builder.alloca(ir.PointerType(ir.IntType(32)), name="p"), "i32*")}

    emit_statement(gen, builder, IndexAssign(Var("p"), IntLiteral(1), IntLiteral(30)), scope)
    body = str(builder.function)
    assert "getelementptr" in body
    assert "store i32 30" in body


def test_index_assign_widens_to_the_element_type(env):
    """
    The stored value coerces to the pointer's element type.
    """
    gen, builder = env
    scope = {"p": Variable(builder.alloca(ir.PointerType(ir.IntType(64)), name="p"), "i64*")}

    emit_statement(gen, builder, IndexAssign(Var("p"), IntLiteral(0), IntLiteral(7)), scope)
    assert "store i64 7" in str(builder.function)


def test_index_assign_to_a_non_pointer_is_an_error(env):
    """
    Index-assigning a value that is not a pointer raises a TypeError.
    """
    gen, builder = env
    scope = {"n": Variable(builder.alloca(ir.IntType(32), name="n"), "i32")}

    with pytest.raises(TypeError, match="cannot index"):
        emit_statement(gen, builder, IndexAssign(Var("n"), IntLiteral(0), IntLiteral(1)), scope)


def test_assign_to_undefined_variable_is_an_error(env):
    """
    Assigning to a name not in scope raises a NameError.
    """
    gen, builder = env
    with pytest.raises(NameError, match="undefined variable 'ghost'"):
        emit_statement(gen, builder, Assign("ghost", IntLiteral(1)), {})


def test_return_with_value_terminates_the_block(env):
    """
    'return expr' terminates the current block.
    """
    gen, builder = env
    emit_statement(gen, builder, Return(IntLiteral(3)), {})
    assert builder.block.is_terminated


def test_return_without_value_emits_ret_void(env):
    """
    A bare return emits ret void.
    """
    gen, builder = env
    emit_statement(gen, builder, Return(None), {})
    assert "ret void" in str(builder.block)


def test_expression_statement_discards_the_value(env):
    """
    An expression statement emits its expression without terminating the block.
    """
    gen, builder = env
    emit_statement(gen, builder, ExprStmt(IntLiteral(1)), {})
    assert not builder.block.is_terminated


def test_unknown_statement_is_an_error(env):
    """
    An unrecognized statement node raises a TypeError.
    """
    gen, builder = env
    with pytest.raises(TypeError, match="cannot generate code for statement"):
        emit_statement(gen, builder, object(), {})


def test_block_emits_statements_in_order(env):
    """
    emit_block runs its statements in source order.
    """
    gen, builder = env
    scope = {}
    emit_block(gen, builder, [Let("a", "i32", None), Let("b", "i32", None)], scope)
    assert list(scope) == ["a", "b"]


def test_block_stops_after_a_terminator(env):
    """
    Statements after a return in the same block are not emitted.
    """
    gen, builder = env
    scope = {}
    emit_block(gen, builder, [Return(IntLiteral(0)), Let("dead", "i32", None)], scope)
    assert "dead" not in scope


def test_if_branches_to_new_blocks(env):
    """
    An if creates then/end blocks and leaves the builder at the end block.
    """
    gen, builder = env
    emit_if(gen, builder, If(IntLiteral(1), [ExprStmt(IntLiteral(0))]), {})
    names = [block.name for block in builder.function.blocks]
    assert names == ["entry", "if.then", "if.end"]
    assert builder.block.name == "if.end"


def test_if_with_else_gets_an_else_block(env):
    """
    An else branch adds an if.else block between then and end.
    """
    gen, builder = env
    stmt = If(IntLiteral(1), [ExprStmt(IntLiteral(0))], [ExprStmt(IntLiteral(0))])
    emit_if(gen, builder, stmt, {})
    names = [block.name for block in builder.function.blocks]
    assert names == ["entry", "if.then", "if.else", "if.end"]


def test_if_condition_is_compared_against_zero(env):
    """
    A non-boolean condition is compared against zero, C-style.
    """
    gen, builder = env
    scope = {"x": Variable(builder.alloca(ir.IntType(32), name="x"), "i32")}
    emit_if(gen, builder, If(Var("x"), [ExprStmt(IntLiteral(0))]), scope)
    assert "icmp" in str(builder.function.blocks[0])


def test_if_where_both_branches_return_marks_the_end_unreachable(env):
    """
    When both branches return, the merge block holds only 'unreachable'.
    """
    gen, builder = env
    stmt = If(IntLiteral(1), [Return(IntLiteral(1))], [Return(IntLiteral(2))])
    emit_if(gen, builder, stmt, {})
    assert "unreachable" in str(builder.block)


def test_if_branch_that_returns_does_not_branch_to_end(env):
    """
    A branch ending in return gets no extra fall-through branch.
    """
    gen, builder = env
    emit_if(gen, builder, If(IntLiteral(1), [Return(IntLiteral(1))]), {})
    then_block = builder.function.blocks[1]
    assert "ret i32 1" in str(then_block)
    assert "br " not in str(then_block)
