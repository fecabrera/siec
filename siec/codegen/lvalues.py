"""Typed assignable places for statement emission."""

from dataclasses import dataclass, field
from typing import Protocol

from llvmlite import ir

from siec.ast import (CachedExpr, Call, Cast, Index, Member, MethodCall,
                      UnaryOp, Var)
from siec.codegen.generator import CodeGenerator, Variable, make_volatile
from siec.codegen.inference import expr_sie_type, item_call, member_field
from siec.codegen.resolution import fold_qualified
from siec.codegen.types import is_const, strip_reference


class LValue(Protocol):
    """The common operations and metadata of an assignable place."""
    target: object
    scope: dict
    type: str | None
    declared_type: str | None
    const: bool
    volatile: bool

    def load(self): ...

    def cached_load(self) -> CachedExpr: ...

    def store(self, value) -> None: ...


def volatile_chain(gen: CodeGenerator, expr, scope: dict) -> bool:
    """
    Whether an lvalue chain passes through a '@volatile' struct: any link
    whose type names one, directly or behind pointers and arrays.
    """
    from siec.codegen.types import strip_const

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
        elif isinstance(node, Cast):
            node = node.operand
        elif isinstance(node, UnaryOp) and node.op == "*":
            node = node.operand
        else:
            return False


def reject_const_base(gen: CodeGenerator, scope: dict, base) -> None:
    """
    Reject mutation through any const link in an lvalue's base chain.
    """
    while True:
        base_type = expr_sie_type(gen, base, scope)
        if is_const(base_type):
            raise TypeError(f"cannot mutate a {base_type!r} value")

        if isinstance(base, (Member, Index)):
            base = base.base
        elif isinstance(base, Cast):
            base = base.operand
        elif isinstance(base, UnaryOp) and base.op == "*":
            base = base.operand
        else:
            return


@dataclass
class AddressLValue:
    """An assignable place represented by one lazily emitted address."""
    gen: CodeGenerator
    builder: ir.IRBuilder
    target: object
    scope: dict
    type: str | None
    declared_type: str | None
    const: bool
    volatile: bool
    _address: object = field(default=None, init=False, repr=False)

    def address(self):
        """Emit this place's address once and retain it for loads and stores."""
        if self._address is None:
            from siec.codegen.expressions import emit_lvalue

            self._address = emit_lvalue(
                self.gen, self.builder, self.target, self.scope)
        return self._address

    def load(self):
        """Load the current value, preserving volatile access metadata."""
        load = self.builder.load(self.address())
        if self.volatile or self.gen.volatile_struct(load.type):
            make_volatile(load)
        return load

    def cached_load(self) -> CachedExpr:
        """An expression node carrying this place's already-loaded value."""
        return CachedExpr(self.target, self.load())

    def store(self, value) -> None:
        """Coerce and store a value through this place's retained address."""
        from siec.codegen.coercion import emit_coerced
        from siec.codegen.expressions import emit_expression

        address = self.address()
        if self.type is not None:
            emitted = emit_coerced(
                self.gen, self.builder, value, self.type, self.scope)
        else:
            emitted = emit_expression(
                self.gen, self.builder, value, address.type.pointee, self.scope)

        store = self.builder.store(emitted, address)
        if self.volatile or self.gen.volatile_struct(emitted.type):
            make_volatile(store)


@dataclass
class ItemLValue:
    """
    A trait-indexed place whose getter returns a value and setter writes it.

    It deliberately has no address. A compound update stabilizes its receiver
    address and caches its key so getter and setter observe one evaluation.
    """
    gen: CodeGenerator
    builder: ir.IRBuilder
    target: Index
    scope: dict
    type: str | None
    declared_type: str | None
    const: bool
    volatile: bool
    has_getter: bool
    has_setter: bool

    def stabilize(self) -> None:
        """Share one receiver and key between a subsequent load and store."""
        stable_scope = dict(self.scope)
        base_type = expr_sie_type(self.gen, self.target.base, self.scope)
        if base_type is None:
            raise TypeError("cannot determine the type of assignment target")

        from siec.codegen.expressions import emit_lvalue

        address = emit_lvalue(
            self.gen, self.builder, self.target.base, self.scope)
        name = f".item.base.{self.gen.temporary_count}"
        self.gen.temporary_count += 1
        stable_scope[name] = Variable(
            address,
            base_type,
            volatile_chain(self.gen, self.target.base, self.scope),
        )
        self.scope = stable_scope
        self.target = Index(Var(name), CachedExpr(self.target.index))

    def getter(self):
        if not self.has_getter:
            raise TypeError("indexed compound assignment requires get_item")
        return MethodCall(
            self.target.base, "get_item", [self.target.index])

    def load(self):
        """Read the indexed value through get_item."""
        from siec.codegen.expressions import emit_expression

        return emit_expression(
            self.gen, self.builder, self.getter(), None, self.scope)

    def cached_load(self) -> CachedExpr:
        """An expression node carrying get_item's already-emitted result."""
        getter = self.getter()
        from siec.codegen.expressions import emit_expression

        value = emit_expression(
            self.gen, self.builder, getter, None, self.scope)
        return CachedExpr(getter, value)

    def store(self, value) -> None:
        """Write the indexed value through set_item."""
        if not self.has_setter:
            raise TypeError("indexed assignment requires set_item")

        from siec.codegen.expressions import emit_expression

        setter = MethodCall(
            self.target.base, "set_item", [self.target.index, value])
        emit_expression(self.gen, self.builder, setter, None, self.scope)


def resolve_lvalue(gen: CodeGenerator, builder: ir.IRBuilder, target,
                   scope: dict, *, item_mode: str = "store",
                   allow_const_init: bool = False) -> LValue:
    """
    Resolve one assignment target without evaluating it.

    `item_mode='update'` selects a trait-indexed place only when both getter
    and setter exist; otherwise native indexing retains its established error.
    """
    if isinstance(target, Member):
        if (folded := fold_qualified(gen, target, scope)) is not None:
            target = folded

    declared_type = expr_sie_type(gen, target, scope)
    value_type = declared_type
    volatile = volatile_chain(gen, target, scope)

    if isinstance(target, Var):
        if target.name in scope:
            variable = scope[target.name]
            declared_type = variable.type
            value_type = strip_reference(declared_type)
            volatile = variable.volatile or volatile
        elif not target.qualified and not gen.sees(target.name):
            raise NameError(f"undefined variable {target.name!r}")
        elif (symbol := gen.resolve_symbol(target.name)) in gen.globals:
            declared_type = gen.globals[symbol]
            value_type = strip_reference(declared_type)
        elif target.name in gen.constants:
            raise TypeError(f"cannot reassign constant {target.name!r}")
        else:
            raise NameError(f"undefined variable {target.name!r}")

        if is_const(declared_type) and not allow_const_init:
            raise TypeError(f"cannot assign to const variable {target.name!r}")

    elif isinstance(target, Member):
        value_type = declared_type = member_field(gen, target, scope)[1]
        if is_const(declared_type):
            raise TypeError(f"cannot assign to const field {target.field!r}")
        reject_const_base(gen, scope, target.base)

    elif isinstance(target, Index):
        reject_const_base(gen, scope, target.base)
        setter = item_call(gen, target, scope, "set_item")
        getter = item_call(gen, target, scope, "get_item")
        if setter is not None and (item_mode != "update" or getter is not None):
            return ItemLValue(
                gen, builder, target, scope, value_type, declared_type,
                False, volatile, getter is not None, True)

    elif isinstance(target, UnaryOp) and target.op == "*":
        reject_const_base(gen, scope, target.operand)

    elif isinstance(target, Cast):
        if is_const(declared_type):
            raise TypeError("cannot assign through a const cast")
        reject_const_base(gen, scope, target.operand)

    elif isinstance(target, (Call, MethodCall)):
        if is_const(declared_type):
            raise TypeError(
                f"cannot assign through a {declared_type!r} reference")

    else:
        raise TypeError(f"expression is not assignable: {target!r}")

    return AddressLValue(
        gen,
        builder,
        target,
        scope,
        value_type,
        declared_type,
        is_const(declared_type),
        volatile,
    )
