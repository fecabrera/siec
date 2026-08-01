"""Ownership state and automatic destruction lowering."""

from dataclasses import dataclass

from llvmlite import ir

from siec.ast import MethodCall, Var
from siec.codegen.generator import CodeGenerator, Variable, entry_alloca
from siec.codegen.interfaces import type_implements
from siec.codegen.types import (is_const, is_reference, strip_const,
                                strip_reference)


@dataclass
class DropCleanup:
    """A local whose initialized storage is destroyed when its scope exits."""
    name: str
    variable: Variable


@dataclass
class TemporaryDrop:
    """A materialized borrowed temporary destroyed after its containing call."""
    slot: object
    type: str
    expression: int | None = None


def destroyable(gen: CodeGenerator, type_name: str | None) -> bool:
    """Whether an owned value carries the nominal Destroy contract."""
    if ("Destroy" not in gen.interfaces
            or type_name is None or is_reference(type_name)
            or is_const(type_name)):
        return False
    return type_implements(
        gen, strip_const(strip_reference(type_name)), "Destroy")


def assign_adopts_parameter(gen: CodeGenerator, fn, position: int) -> bool:
    """Whether this parameter is Assign<T>'s receiver-adopted source."""
    if position != 1 or len(fn.params) != 2:
        return False
    if fn.name.partition("::")[2] != "assign":
        return False

    receiver = fn.receiver or fn.name.partition("::")[0]
    if not receiver:
        return False
    source = strip_const(strip_reference(fn.params[position].type))
    return type_implements(gen, receiver, f"Assign<{source}>")


def new_drop_flag(builder, name: str, initialized: bool = False):
    """Allocate and initialize one runtime ownership bit."""
    flag = entry_alloca(builder, ir.IntType(1), f"{name}.drop")
    builder.store(ir.Constant(ir.IntType(1), int(initialized)), flag)
    return flag


def set_drop_flag(builder, variable: Variable, active: bool) -> None:
    """Arm or disarm a variable's automatic cleanup when it has one."""
    if variable.drop_flag is not None:
        builder.store(
            ir.Constant(ir.IntType(1), int(active)), variable.drop_flag)


def emit_drop_slot(gen: CodeGenerator, builder, slot, type_name: str) -> None:
    """Invoke Destroy::destroy for one known-initialized storage slot."""
    from siec.codegen.expressions import emit_expression

    name = f".drop.value.{gen.temporary_count}"
    gen.temporary_count += 1
    scope = {name: Variable(slot, type_name)}
    emit_expression(
        gen, builder, MethodCall(Var(name), "destroy", []), None, scope)


def emit_drop_cleanup(gen: CodeGenerator, builder,
                      cleanup: DropCleanup) -> None:
    """Destroy an initialized local and permanently disarm its flag."""
    variable = cleanup.variable
    if variable.drop_flag is None:
        return

    active = builder.load(variable.drop_flag, name=f"{cleanup.name}.owned")
    with builder.if_then(active):
        emit_drop_slot(gen, builder, variable.slot, variable.type)
        set_drop_flag(builder, variable, False)


def emit_temporary_drop(gen: CodeGenerator, builder,
                        cleanup: TemporaryDrop) -> None:
    """Destroy a temporary known to remain owned by the caller."""
    emit_drop_slot(gen, builder, cleanup.slot, cleanup.type)


def begin_temporary_frame(gen: CodeGenerator) -> bool:
    """Open the outermost temporary lifetime for a full expression."""
    owns = not gen.borrowed_temporary_frames
    if owns:
        gen.borrowed_temporary_frames.append([])
    return owns


def finish_temporary_frame(gen: CodeGenerator, builder, owns: bool) -> None:
    """Destroy one full expression's remaining temporaries in reverse."""
    if not owns:
        return
    cleanups = gen.borrowed_temporary_frames.pop()
    for cleanup in reversed(cleanups):
        emit_temporary_drop(gen, builder, cleanup)


def register_address_temporary(gen: CodeGenerator, slot,
                               type_name: str | None, expr=None) -> None:
    """Retain a constructor address until its full expression finishes."""
    if (gen.borrowed_temporary_frames
            and destroyable(gen, type_name)):
        gen.borrowed_temporary_frames[-1].append(
            TemporaryDrop(
                slot, strip_const(type_name),
                id(expr) if expr is not None else None))


def track_value_temporary(gen: CodeGenerator, builder, expr, value,
                          type_name: str | None):
    """Give a destructible call result storage through its full expression."""
    if (not gen.borrowed_temporary_frames
            or not destroyable(gen, type_name)
            or expression_returns_reference(gen, expr)):
        return value

    slot = entry_alloca(builder, value.type, "owned.temporary")
    builder.store(value, slot)
    gen.borrowed_temporary_frames[-1].append(
        TemporaryDrop(slot, strip_const(type_name), id(expr)))
    return builder.load(slot, name="owned.value")


def consume_temporary(gen: CodeGenerator, expr) -> None:
    """Remove a full-expression temporary transferred into another owner."""
    if not gen.borrowed_temporary_frames:
        return
    identity = id(expr)
    frame = gen.borrowed_temporary_frames[-1]
    for index in range(len(frame) - 1, -1, -1):
        if frame[index].expression == identity:
            frame.pop(index)
            return


def temporary_registered(gen: CodeGenerator, expr) -> bool:
    """Whether this expression already owns one tracked temporary slot."""
    identity = id(expr)
    return bool(gen.borrowed_temporary_frames and any(
        cleanup.expression == identity
        for cleanup in gen.borrowed_temporary_frames[-1]
    ))


def disarm_expression(gen: CodeGenerator, builder, expr, scope: dict) -> None:
    """Transfer ownership out of a whole local expression when applicable."""
    from siec.ast import Move

    source = expr.operand if isinstance(expr, Move) else expr
    if not isinstance(source, Var) or source.name not in scope:
        return
    variable = scope[source.name]
    if destroyable(gen, variable.type):
        set_drop_flag(builder, variable, False)


def expression_returns_reference(gen: CodeGenerator, expr) -> bool:
    """Whether a checked call expression aliases callee-owned storage."""
    symbol = getattr(expr, "resolved_symbol", None)
    return symbol is not None and is_reference(gen.return_types.get(symbol))


def manually_destroyed_local(expr) -> str | None:
    """The whole local consumed by a direct ``local.destroy()`` call."""
    from siec.ast import Call, MethodCall, Var

    if (isinstance(expr, MethodCall) and expr.method == "destroy"
            and isinstance(expr.receiver, Var)):
        return expr.receiver.name
    if isinstance(expr, Call) and expr.name.endswith(".destroy"):
        base = expr.name.removesuffix(".destroy")
        if base.isidentifier():
            return base
    return None
