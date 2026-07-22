"""Conversion of expression values: implicit coercion and explicit casts."""

from llvmlite import ir

from siec.ast import (
    AggregateLiteral,
    ArrayLiteral,
    BlockExpr,
    Call,
    Cast,
    Expr,
    Member,
    MethodCall,
    TupleLiteral,
    Var,
)
from siec.codegen.aliases import expand_alias
from siec.codegen.generator import CodeGenerator
from siec.codegen.inference import (
    enum_backing,
    expr_sie_type,
    numeric_class,
    type_info,
    value_class,
)
from siec.codegen.types import (
    is_aliasing,
    is_array_struct,
    is_const,
    resolve_type,
    strip_const,
    strip_reference,
)


def emit_opaque_pointer(builder: ir.IRBuilder, value: ir.Value, target_type: ir.Type):
    """
    Lower a pointer or array value to 'opaque*': an array goes through its
    data pointer, and any pointer bitcasts to the untyped target.

    Returns None for values that are neither, for the caller to reject.
    """
    if is_array_struct(value.type):
        value = builder.extract_value(value, 0, name="decay")

    if isinstance(value.type, ir.PointerType):
        return builder.bitcast(value, target_type)

    return None


def emit_any_wrap(gen: CodeGenerator, builder: ir.IRBuilder, expr: Cast,
                  scope: dict):
    """
    Emit 'v as Any': the value spills to a slot of the enclosing
    function's frame, and the Any pairs its type's id with the slot's
    address. Wrapping an Any again is the same value.
    """
    from siec.codegen.expressions import emit_expression, fnv1a
    from siec.codegen.generator import entry_alloca
    from siec.codegen.inference import infer_type

    source = infer_type(gen, expr.operand, scope)
    if source is None:
        raise TypeError("cannot wrap in 'Any': the expression has no type")

    source = strip_const(strip_reference(source))
    value = emit_expression(gen, builder, expr.operand, None, scope)
    if source == "Any":
        return value

    slot = entry_alloca(builder, value.type, "any.data")
    builder.store(value, slot)

    any_type = resolve_type("Any", gen.structs)
    wrapped = ir.Constant(any_type, None)
    wrapped = builder.insert_value(
        wrapped, ir.Constant(ir.IntType(64), fnv1a(source)), 0, name="any.id")
    return builder.insert_value(
        wrapped, builder.bitcast(slot, any_type.elements[1]), 1, name="any.data")


def emit_cast(gen: CodeGenerator, builder: ir.IRBuilder, expr: Cast, scope: dict):
    """
    Emit an explicit numeric cast, choosing the LLVM conversion the prefixes and widths call for.

    The 'const' contract survives casts: an aliasing const value (a pointer
    or array) never becomes mutable, not even explicitly.
    """
    # deferred import: coercion and expressions are mutually recursive
    from siec.codegen.expressions import emit_expression

    # 'v as Any' wraps the value with its type's id, an 'Any<T>' pair
    if strip_const(expr.type) == "Any":
        return emit_any_wrap(gen, builder, expr, scope)

    # the written spelling expands (and gates) once; the canonical
    # result must not re-gate as if written here
    if not getattr(expr, "expanded", False):
        expr.type = expand_alias(gen, expr.type)
        expr.expanded = True

    operand_name = expr_sie_type(gen, expr.operand, scope)

    # 'a as T' on an Any reads its erased value back as T, unchecked:
    # comparing '@typeof(a)' first is the caller's job
    if (operand_name is not None
            and strip_const(strip_reference(operand_name)) == "Any"):
        value = emit_expression(gen, builder, expr.operand, None, scope)
        data = builder.extract_value(value, 1, name="any.data")
        typed = builder.bitcast(
            data, ir.PointerType(resolve_type(expr.type, gen.structs)))
        return builder.load(typed, name="any.value")

    if (is_const(operand_name) and not is_const(expr.type)
            and is_aliasing(strip_const(operand_name))):
        raise TypeError(f"cannot cast away 'const': {operand_name!r} to {expr.type!r}")

    # a cast to an enum type converts to its backing type
    target = numeric_class(enum_backing(gen, expr.type))
    if target is None:
        # an array casts to its element pointer: 'arr as X*' extracts the data field
        target_type = resolve_type(expr.type, gen.structs)
        value = emit_expression(gen, builder, expr.operand, None, scope)

        # casting a value to its own represented type is a no-op
        source = strip_const(operand_name)
        if source is not None and source == strip_const(expr.type):
            return value

        if (isinstance(target_type, ir.PointerType) and is_array_struct(value.type)
                and value.type.elements[0] == target_type):
            return builder.extract_value(value, 0, name="decay")

        # the modifier plays no part in what the cast does; only the
        # represented type directs the conversion
        target_name = strip_const(expr.type)

        # any pointer casts to 'opaque*', the same way it decays to it
        if target_name == "opaque*":
            decayed = emit_opaque_pointer(builder, value, target_type)
            if decayed is not None:
                return decayed

        # an 'opaque*' casts to any pointer, the reverse of the decay
        if (isinstance(target_type, ir.PointerType)
                and strip_const(operand_name) == "opaque*"):
            return builder.bitcast(value, target_type)

        # 'i8[]'/'u8[]' and 'char[]' cast into each other, the length
        # adjusting for the null terminator a char[] excludes; the
        # underlying pointer is assumed null-terminated
        source_name = strip_const(operand_name)
        delta = None
        if target_name == "char[]" and source_name in ("i8[]", "u8[]"):
            delta = -1
        elif target_name in ("i8[]", "u8[]") and source_name == "char[]":
            delta = 1

        if delta is not None:
            length = builder.extract_value(value, 1, name="cast.len")
            adjusted = builder.add(length, ir.Constant(ir.IntType(64), delta))
            return builder.insert_value(value, adjusted, 1)

        raise TypeError(f"cannot cast to non-numeric type {expr.type!r}")

    value = emit_expression(gen, builder, expr.operand, None, scope)
    source = value_class(gen, value, expr.operand, scope)
    if source is None:
        # a bare integer literal has no declared type and reads as a signed
        # integer; a named non-numeric value (a char or bool) cannot be cast
        declared = expr_sie_type(gen, expr.operand, scope)
        if declared is None and isinstance(value.type, ir.IntType) and value.type.width > 1:
            source = "i", value.type.width
        else:
            raise TypeError(f"cannot cast a non-numeric value to {expr.type}")

    source_prefix, source_width = source
    target_prefix, target_width = target
    target_type = resolve_type(expr.type, gen.structs)

    # float to float: extend or truncate the mantissa
    if source_prefix == "f" and target_prefix == "f":
        if target_width > source_width:
            return builder.fpext(value, target_type)

        if target_width < source_width:
            return builder.fptrunc(value, target_type)

        return value

    # float to integer, and integer to float, honoring each side's signedness
    if source_prefix == "f":
        convert = builder.fptosi if target_prefix == "i" else builder.fptoui
        return convert(value, target_type)

    if target_prefix == "f":
        convert = builder.sitofp if source_prefix == "i" else builder.uitofp
        return convert(value, target_type)

    # integer to integer: widen by the source's sign, narrow by truncation
    if target_width > source_width:
        extend = builder.sext if source_prefix == "i" else builder.zext
        return extend(value, target_type)

    if target_width < source_width:
        return builder.trunc(value, target_type)

    # same width: a reinterpretation between prefixes, no instruction needed
    return value


def emit_coerced(gen: CodeGenerator, builder: ir.IRBuilder, expr: Expr,
                 target_name: str | None, scope: dict):
    """
    Emit an expression for a typed context, implicitly widening it to the target when allowed.

    A numeric value widens to a larger type of the same prefix (i/u/f); crossing
    prefixes or narrowing needs an explicit cast and is rejected here.

    'const T' is a contract, not a type: a mutable T passes as const T freely,
    but an aliasing const value (a pointer or array) never passes where a
    mutable one is expected - only an explicit cast sheds the contract.
    """
    # deferred import: coercion and expressions are mutually recursive
    from siec.codegen.expressions import (
        emit_aggregate,
        emit_array,
        emit_block_expr,
        emit_expression,
    )

    # the target may drive a generic callee's type arguments where its
    # own cannot: 'let r: Result<i32, u8> = Ok(5);' binds E from the target
    if isinstance(expr, (Call, MethodCall)) and target_name is not None:
        expr.expected_type = strip_reference(strip_const(target_name))

    source_name = expr_sie_type(gen, expr, scope)
    if (target_name is not None and is_const(source_name) and not is_const(target_name)
            and is_aliasing(strip_const(source_name))):
        raise TypeError(f"cannot use a {source_name!r} value where a mutable "
                        f"{target_name!r} is expected")

    const_target = is_const(target_name)
    target_name = strip_const(target_name)
    target_type = resolve_type(target_name, gen.structs)

    # an aggregate literal coerces each element to its field's type instead
    if isinstance(expr, AggregateLiteral):
        info = type_info(gen, target_name)
        if info is not None and info.is_union:
            raise TypeError("a union takes no aggregate literal; assign one "
                            "of its fields instead")

        # a const target views its aliasing fields as const, the same way
        # member access does, so a const pointer can fill a const array
        field_names = (
            [f"const {f.type}"
             if const_target and is_aliasing(f.type) and not is_const(f.type)
             else f.type
             for f in info.fields]
            if info is not None
            else None
        )
        return emit_aggregate(gen, builder, expr, target_type, scope, field_names)

    # a block expression coerces each emitted value to the target instead
    if isinstance(expr, BlockExpr):
        return emit_block_expr(gen, builder, expr, target_type, scope, target_name)

    # a tuple literal coerces each element to the target's element type
    if isinstance(expr, TupleLiteral) and target_name is not None:
        from siec.codegen.expressions import emit_tuple

        return emit_tuple(gen, builder, expr, scope, target_name)

    # an array literal coerces each element to the array's declared element type
    if isinstance(expr, ArrayLiteral):
        # in a pointer context, the literal builds its array and decays to
        # its data pointer: 'let ptr: i32* = [1, 2, 3];'
        if isinstance(target_type, ir.PointerType):
            array_type = ir.LiteralStructType([target_type, ir.IntType(64)])
            element_name = target_name.removesuffix("*") if target_name else None
            value = emit_array(gen, builder, expr, array_type, scope, element_name)
            return builder.extract_value(value, 0, name="decay")

        element_name = target_name[:-2] if target_name and target_name.endswith("[]") else None
        return emit_array(gen, builder, expr, target_type, scope, element_name)

    # a bare generic function adopts a function-typed context, its type
    # arguments unified from the target's signature; a dotted spelling
    # folds through its module binding first
    if target_name is not None and target_name.startswith("fn("):
        from siec.codegen.generics import reference_for_target
        from siec.codegen.inference import fold_qualified

        candidate = fold_qualified(gen, expr, scope) if isinstance(expr, Member) else expr
        if (isinstance(candidate, Var) and candidate.type_args is None
                and candidate.name not in scope):
            func = reference_for_target(gen, candidate, target_name)
            if func is not None:
                return func

    value = emit_expression(gen, builder, expr, target_type, scope)

    # an array lowers to its data pointer where a plain pointer is expected
    if (isinstance(target_type, ir.PointerType) and is_array_struct(value.type)
            and value.type.elements[0] == target_type):
        return builder.extract_value(value, 0, name="decay")

    # any pointer decays to 'opaque*', the void*-style catch-all
    if target_name == "opaque*":
        decayed = emit_opaque_pointer(builder, value, target_type)
        if decayed is not None:
            return decayed

    # only scalar numeric targets widen; everything else demands an exact match
    target = numeric_class(target_name)
    if target is None:
        if target_name is not None and value.type != target_type:
            raise TypeError(f"cannot implicitly convert {value.type} to {target_name}")

        return value

    # a literal has no inherent prefix and simply adopts the target; anything
    # unclassifiable (a char, say) must already be exactly the target type
    source = value_class(gen, value, expr, scope)
    if source is None:
        if value.type == target_type:
            return value
        raise TypeError(f"cannot implicitly convert {value.type} to {target_name}")

    source_prefix, source_width = source
    target_prefix, target_width = target

    if source_prefix != target_prefix:
        raise TypeError(f"cannot implicitly convert {source_prefix}{source_width} to "
                        f"{target_prefix}{target_width}: use an explicit cast between "
                        "signed, unsigned, and float types")

    if source_width > target_width:
        raise TypeError(f"cannot implicitly narrow {source_prefix}{source_width} to "
                        f"{target_prefix}{target_width}: use an explicit cast")

    if source_width == target_width:
        return value

    # same prefix, wider target: extend by the kind the prefix calls for
    extend = {"i": builder.sext, "u": builder.zext, "f": builder.fpext}[target_prefix]
    return extend(value, target_type)
