"""Compiler lowering for the builtin ``Slot<T>`` raw-storage operations."""

from llvmlite import ir

from siec.ast import MethodCall, Var
from siec.codegen.generator import CodeGenerator, Variable, make_volatile
from siec.codegen.generics import split_generic
from siec.codegen.types import strip_const, strip_reference


def slot_function(fn) -> tuple[str, str] | None:
    """Return an intrinsic slot function's element and method names."""
    receiver = strip_const(strip_reference(
        fn.params[0].type if fn.params else fn.receiver or ""))
    parts = split_generic(receiver)
    if parts is None or parts[0] != "Slot" or len(parts[1]) != 1:
        return None
    return parts[1][0], fn.name.partition("::")[2]


def check_slot_function(gen: CodeGenerator, fn) -> bool:
    """Semantically check a concrete builtin slot operation."""
    found = slot_function(fn)
    if found is None:
        return False

    element, method = found
    from siec.codegen.ownership import destroyable

    if method == "assign_to" and destroyable(gen, element):
        from siec.codegen.checking import (check_expression,
                                           checked_variable)
        from siec.codegen.interfaces import type_implements

        source = ".slot.source"
        target = ".slot.target"
        scope = {
            source: checked_variable(f"const &{element}"),
            target: checked_variable(f"&{element}"),
        }
        if type_implements(gen, element, f"AssignFrom<{element}>"):
            call = MethodCall(Var(target), "assign_from", [Var(source)])
            check_expression(gen, call, scope)
            fn.slot_assign_call = call
        elif type_implements(gen, element, "Clone"):
            call = MethodCall(Var(source), "clone", [])
            check_expression(gen, call, scope, element)
            fn.slot_clone_call = call
            from siec.codegen.checking import check_temporary_cleanup

            check_temporary_cleanup(gen, element, {})
        else:
            raise TypeError(
                f"cannot assign owned {element!r} value from a Slot: "
                f"implement AssignFrom<{element}> or Clone")

    if method == "write_from":
        from siec.codegen.interfaces import type_implements

        if (destroyable(gen, element)
                and not type_implements(gen, element, "Clone")):
            raise TypeError(f"cannot copy owned {element!r} value into a Slot: "
                            "implement Clone or use write with an owned value")

        if destroyable(gen, element):
            from siec.codegen.checking import (check_expression,
                                               checked_variable)

            name = ".slot.source"
            call = MethodCall(Var(name), "clone", [])
            check_expression(
                gen, call,
                {name: checked_variable(f"const &{element}")}, element)
            fn.slot_clone_call = call

    if method in ("drop", "replace") and destroyable(gen, element):
        from siec.codegen.checking import check_temporary_cleanup

        check_temporary_cleanup(gen, element, {})
    return True


def emit_slot_function(gen: CodeGenerator, builder: ir.IRBuilder,
                       fn, func: ir.Function) -> bool:
    """Emit one concrete slot operation directly over its raw field."""
    found = slot_function(fn)
    if found is None:
        return False

    element, method = found
    zero = ir.Constant(ir.IntType(32), 0)
    storage = builder.gep(
        func.args[0], [zero, zero], inbounds=True, name="slot.value")

    def load(name: str):
        value = builder.load(storage, name=name)
        if gen.volatile_struct(element):
            make_volatile(value)
        return value

    def store(value) -> None:
        instruction = builder.store(value, storage)
        if gen.volatile_struct(element):
            make_volatile(instruction)

    if method in ("get", "get_mut"):
        builder.ret(storage)
        return True

    if method == "take":
        builder.ret(load("slot.take"))
        return True

    if method == "assign_to":
        from siec.codegen.interfaces import type_implements
        from siec.codegen.ownership import destroyable, emit_drop_slot

        if (destroyable(gen, element)
                and type_implements(
                    gen, element, f"AssignFrom<{element}>")):
            from siec.codegen.expressions import emit_expression

            source = ".slot.source"
            target = ".slot.target"
            scope = {
                source: Variable(storage, f"const &{element}"),
                target: Variable(func.args[1], f"&{element}"),
            }
            emit_expression(
                gen, builder, fn.slot_assign_call, None, scope)
            builder.ret_void()
            return True

        if destroyable(gen, element):
            from siec.codegen.expressions import emit_expression

            source = ".slot.source"
            scope = {
                source: Variable(storage, f"const &{element}"),
            }
            value = emit_expression(
                gen, builder, fn.slot_clone_call, None, scope)
            emit_drop_slot(gen, builder, func.args[1], element)
        else:
            value = load("slot.copy")

        instruction = builder.store(value, func.args[1])
        if gen.volatile_struct(element):
            make_volatile(instruction)
        builder.ret_void()
        return True

    if method in ("drop", "replace"):
        from siec.codegen.ownership import destroyable, emit_drop_slot

        if destroyable(gen, element):
            emit_drop_slot(gen, builder, storage, element)

        if method == "drop":
            builder.ret_void()
            return True

    if method == "write_from":
        from siec.codegen.ownership import destroyable

        if destroyable(gen, element):
            from siec.codegen.expressions import emit_expression

            name = ".slot.source"
            scope = {
                name: Variable(func.args[1], f"const &{element}"),
            }
            value = emit_expression(
                gen, builder, fn.slot_clone_call, None, scope)
        else:
            value = builder.load(func.args[1], name="slot.copy")
            if gen.volatile_struct(element):
                make_volatile(value)
    else:
        value = func.args[1]

    store(value)
    builder.ret_void()
    return True
