"""Emission of expressions: the dispatcher, lvalues, literals, and operators.

Type analysis lives in siec.codegen.inference, conversions in
siec.codegen.coercion, and calls in siec.codegen.calls.
"""

from llvmlite import ir

from siec.ast import (
    AggregateLiteral,
    ArrayLiteral,
    BinaryOp,
    BlockExpr,
    BoolLiteral,
    Call,
    Cast,
    CharLiteral,
    EnumMember,
    Expr,
    FloatLiteral,
    Index,
    IntLiteral,
    Member,
    NullLiteral,
    SizeOf,
    Slice,
    StrLiteral,
    Ternary,
    UnaryOp,
    Var,
)
from siec.codegen.calls import emit_call
from siec.codegen.coercion import emit_cast, emit_coerced
from siec.codegen.enums import member_value
from siec.codegen.generator import CodeGenerator, entry_alloca, make_volatile
from siec.codegen.sizes import size_of
from siec.codegen.inference import (
    ARITHMETIC,
    FLOAT_ARITHMETIC,
    UNSIGNED_ARITHMETIC,
    check_signedness,
    is_float,
    member_field,
)
from siec.codegen.types import is_array_struct, is_reference, resolve_type


def emit_expression(gen: CodeGenerator, builder: ir.IRBuilder, expr: Expr,
                    expected_type: ir.Type | None, scope: dict):
    """
    Emit an expression, coercing literals to expected_type when given.
    """
    # dispatch on the node type; each branch returns an LLVM value
    if isinstance(expr, IntLiteral):
        # integer literals take the type of their context, defaulting to
        # i32; a float context adopts them like C's promotions do
        if is_float(expected_type):
            return ir.Constant(expected_type, float(expr.value))

        int_type = expected_type if isinstance(expected_type, ir.IntType) else ir.IntType(32)
        return ir.Constant(int_type, expr.value)

    if isinstance(expr, FloatLiteral):
        # float literals take the float type of their context, defaulting to f64
        float_type = expected_type if is_float(expected_type) else ir.DoubleType()
        return ir.Constant(float_type, expr.value)

    if isinstance(expr, StrLiteral):
        ptr = emit_string(gen, builder, expr.value)

        # a string literal fills a 'char[]' context as the fat {char*, u64}
        # value, its length excluding the null terminator
        if is_array_struct(expected_type) and expected_type.elements[0] == ptr.type:
            value = ir.Constant(expected_type, ir.Undefined)
            value = builder.insert_value(value, ptr, 0)
            return builder.insert_value(
                value, ir.Constant(ir.IntType(64), len(expr.value.encode())), 1)

        return ptr

    if isinstance(expr, BoolLiteral):
        # boolean literals are i1 constants, independent of the context type
        return ir.Constant(ir.IntType(1), 1 if expr.value else 0)

    if isinstance(expr, CharLiteral):
        # a char literal is exactly a 'char': one byte, its own type
        return ir.Constant(ir.IntType(8), expr.value.encode()[0])

    if isinstance(expr, NullLiteral):
        # 'null' adopts whatever pointer context it lands in, an opaque*
        # on its own
        if expected_type is None or isinstance(expected_type, ir.PointerType):
            return ir.Constant(expected_type or ir.PointerType(ir.IntType(8)), None)

        raise TypeError("'null' needs a pointer context")

    if isinstance(expr, EnumMember):
        # an enum member adopts an integer context like a literal would,
        # defaulting to its enum's backing type
        value = member_value(gen, expr)
        if isinstance(expected_type, ir.IntType):
            return ir.Constant(expected_type, value)

        return ir.Constant(resolve_type(gen.enums[expr.enum].backing), value)

    if isinstance(expr, SizeOf):
        # 'sizeof' is a compile-time constant adopting an integer context
        # like a literal, defaulting to u64
        size = size_of(gen, expr.name, scope)
        if isinstance(expected_type, ir.IntType):
            return ir.Constant(expected_type, size)

        return ir.Constant(ir.IntType(64), size)

    if isinstance(expr, AggregateLiteral):
        return emit_aggregate(gen, builder, expr, expected_type, scope)

    if isinstance(expr, BlockExpr):
        return emit_block_expr(gen, builder, expr, expected_type, scope)

    if isinstance(expr, ArrayLiteral):
        return emit_array(gen, builder, expr, expected_type, scope)

    if isinstance(expr, Var):
        # variables load their current value from their stack slot; a
        # '@volatile' struct's loads are never elided or reordered
        if expr.name in scope:
            load = builder.load(scope[expr.name].slot, name=expr.name)
            if gen.volatile_struct(load.type):
                make_volatile(load)

            return load

        # a constant substitutes its value expression in place, coerced to
        # its annotated type when it has one, adapting like a literal otherwise
        const = gen.constants.get(expr.name)
        if const is not None:
            if const.type is not None:
                return emit_coerced(gen, builder, const.value, const.type, scope)

            return emit_expression(gen, builder, const.value, expected_type, scope)

        # a global loads its current value from its storage; the current
        # file's statics resolve first, other files' never
        symbol = gen.resolve_symbol(expr.name)
        if symbol in gen.globals:
            load = builder.load(gen.module.globals[symbol], name=expr.name)
            if gen.volatile_struct(load.type):
                make_volatile(load)

            return load

        # a bare function name is a reference to that function
        func = gen.module.globals.get(symbol)
        if isinstance(func, ir.Function):
            return func

        raise NameError(f"undefined variable {expr.name!r}")

    if isinstance(expr, Call):
        return emit_call(gen, builder, expr, scope)

    if isinstance(expr, Index):
        # the element context implies the base's array shape, which is what
        # gives a literal base ('{ptr, n}[1]', say) its type
        base_context = None
        if expected_type is not None and not isinstance(expected_type, ir.VoidType):
            base_context = ir.LiteralStructType([ir.PointerType(expected_type),
                                                 ir.IntType(64)])

        # pointer indexing, C-style: offset the base pointer and load the
        # element; an array indexes through its data pointer
        base = emit_expression(gen, builder, expr.base, base_context, scope)
        if is_array_struct(base.type):
            base = builder.extract_value(base, 0, name="index.data")

        if not isinstance(base.type, ir.PointerType):
            raise TypeError(f"cannot index a value of type {base.type}")

        index = emit_expression(gen, builder, expr.index, ir.IntType(64), scope)
        load = builder.load(builder.gep(base, [index]))
        if gen.volatile_struct(load.type):
            make_volatile(load)

        return load

    if isinstance(expr, Slice):
        return emit_slice(gen, builder, expr, expected_type, scope)

    if isinstance(expr, Member):
        # read a struct or array field: extract it from the base value by index
        index = member_field(gen, expr, scope)[0]
        base = emit_expression(gen, builder, expr.base, None, scope)
        return builder.extract_value(base, index, name=expr.field)

    if isinstance(expr, Cast):
        return emit_cast(gen, builder, expr, scope)

    if isinstance(expr, UnaryOp):
        # unary minus negates in the context type; '~' flips bits; 'not' inverts a bool
        if expr.op == "-":
            value = emit_expression(gen, builder, expr.operand, expected_type, scope)
            return builder.fneg(value) if is_float(value.type) else builder.neg(value)

        if expr.op == "~":
            value = emit_expression(gen, builder, expr.operand, expected_type, scope)
            if is_float(value.type):
                raise TypeError("cannot apply '~' to a float operand")

            return builder.not_(value)

        if expr.op == "not":
            return builder.not_(emit_bool(gen, builder, expr.operand, scope))

        # '&' takes the address of an assignable expression: its stack slot;
        # a reference parameter is not dereferenceable — no address rooted
        # at it may be taken ('&s' and '&s.member' would both leak the
        # caller's storage)
        if expr.op == "&":
            root = expr.operand
            while isinstance(root, (Member, Index)):
                root = root.base

            if (isinstance(root, Var) and root.name in scope
                    and is_reference(scope[root.name].type)):
                through = "of" if root is expr.operand else "through"
                raise TypeError(f"cannot take an address {through} reference "
                                f"parameter {root.name!r}")

            return emit_lvalue(gen, builder, expr.operand, scope)

        raise TypeError(f"unknown unary operator {expr.op!r}")

    if isinstance(expr, Ternary):
        return emit_ternary(gen, builder, expr, expected_type, scope)

    if isinstance(expr, BinaryOp):
        # logical operators coerce each side to a bool on its own terms
        if expr.op in ("and", "or"):
            return emit_logical(gen, builder, expr, scope)

        # every other operator combines the operand values directly and
        # emits unsigned instructions when the operands are unsigned
        unsigned = check_signedness(gen, expr, scope) == "unsigned"

        if expr.op in ARITHMETIC:
            # arithmetic and bitwise: both sides share the context type; the result keeps it
            left = emit_expression(gen, builder, expr.left, expected_type, scope)
            right = emit_expression(gen, builder, expr.right, left.type, scope)

            # float operands take the float instructions; bitwise has none
            if is_float(left.type):
                if expr.op not in FLOAT_ARITHMETIC:
                    raise TypeError(f"cannot apply {expr.op!r} to float operands")

                return getattr(builder, FLOAT_ARITHMETIC[expr.op])(left, right)

            method = UNSIGNED_ARITHMETIC[expr.op] if (
                unsigned and expr.op in UNSIGNED_ARITHMETIC) else ARITHMETIC[expr.op]

            return getattr(builder, method)(left, right)

        if expr.op == "**":
            return emit_power(gen, builder, expr, expected_type, scope, unsigned)

        # comparisons: type the right side by the left, yield an i1
        left = emit_expression(gen, builder, expr.left, None, scope)
        right = emit_expression(gen, builder, expr.right, left.type, scope)

        if is_float(left.type):
            return builder.fcmp_ordered(expr.op, left, right)

        compare = builder.icmp_unsigned if unsigned else builder.icmp_signed
        return compare(expr.op, left, right)

    raise TypeError(f"cannot generate code for {expr!r}")


def emit_lvalue(gen: CodeGenerator, builder: ir.IRBuilder, expr: Expr, scope: dict):
    """
    Emit the address of an assignable expression: a variable, a struct/array
    field, or a pointer-indexed element.
    """
    if isinstance(expr, Var):
        if expr.name in scope:
            return scope[expr.name].slot

        # a global's slot is its module-level storage
        symbol = gen.resolve_symbol(expr.name)
        if symbol in gen.globals:
            return gen.module.globals[symbol]

        raise NameError(f"undefined variable {expr.name!r}")

    if isinstance(expr, Member):
        # index into the base's address: gep past the aggregate to the field slot
        index = member_field(gen, expr, scope)[0]
        base = emit_lvalue(gen, builder, expr.base, scope)
        return builder.gep(base, [ir.Constant(ir.IntType(32), 0),
                                  ir.Constant(ir.IntType(32), index)], name=expr.field)

    if isinstance(expr, Index):
        # offset the base pointer's value to the element's address, C-style;
        # an array's elements are addressed through its data pointer
        base = emit_expression(gen, builder, expr.base, None, scope)
        if is_array_struct(base.type):
            base = builder.extract_value(base, 0, name="index.data")

        if not isinstance(base.type, ir.PointerType):
            raise TypeError(f"cannot index a value of type {base.type}")

        index = emit_expression(gen, builder, expr.index, ir.IntType(64), scope)
        return builder.gep(base, [index])

    raise TypeError(f"expression is not assignable: {expr!r}")


def emit_bool(gen: CodeGenerator, builder: ir.IRBuilder, expr: Expr, scope: dict):
    """
    Emit an expression coerced to a bool: numbers compare against zero,
    C-style, and pointers against null.
    """
    value = emit_expression(gen, builder, expr, ir.IntType(1), scope)

    if isinstance(value.type, ir.PointerType):
        return builder.icmp_unsigned("!=", value, ir.Constant(value.type, None))

    if is_float(value.type):
        return builder.fcmp_ordered("!=", value, ir.Constant(value.type, 0))

    if value.type != ir.IntType(1):
        value = builder.icmp_signed("!=", value, ir.Constant(value.type, 0))

    return value


def emit_power(gen: CodeGenerator, builder: ir.IRBuilder, expr: BinaryOp,
               expected_type: ir.Type | None, scope: dict, unsigned: bool = False):
    """
    Emit '**' as a multiply loop, since LLVM has no integer power instruction.
    """
    base = emit_expression(gen, builder, expr.left, expected_type, scope)
    if is_float(base.type):
        raise TypeError("cannot apply '**' to float operands")

    exp = emit_expression(gen, builder, expr.right, base.type, scope)

    # the result and the remaining exponent live in slots driven by the loop
    result = entry_alloca(builder, base.type, "pow.result")
    remaining = entry_alloca(builder, exp.type, "pow.exp")
    builder.store(ir.Constant(base.type, 1), result)
    builder.store(exp, remaining)

    func = builder.function
    cond_block = func.append_basic_block("pow.cond")
    body_block = func.append_basic_block("pow.body")
    end_block = func.append_basic_block("pow.end")

    builder.branch(cond_block)

    # keep multiplying while the exponent is positive
    builder.position_at_end(cond_block)
    count = builder.load(remaining)

    compare = builder.icmp_unsigned if unsigned else builder.icmp_signed
    builder.cbranch(compare(">", count, ir.Constant(exp.type, 0)),
                    body_block, end_block)

    builder.position_at_end(body_block)
    builder.store(builder.mul(builder.load(result), base), result)
    builder.store(builder.sub(count, ir.Constant(exp.type, 1)), remaining)
    builder.branch(cond_block)

    builder.position_at_end(end_block)
    return builder.load(result)


def emit_ternary(gen: CodeGenerator, builder: ir.IRBuilder, expr: Ternary,
                 expected_type: ir.Type | None, scope: dict):
    """
    Emit 'cond ? then : orelse' as a branch joined by a phi: only the
    chosen arm is evaluated, and both must agree on their type.
    """
    cond = emit_bool(gen, builder, expr.condition, scope)

    func = builder.function
    then_block = func.append_basic_block("ternary.then")
    else_block = func.append_basic_block("ternary.else")
    end_block = func.append_basic_block("ternary.end")

    builder.cbranch(cond, then_block, else_block)

    # each arm may open blocks of its own; the phi needs its exit block
    builder.position_at_end(then_block)
    then_value = emit_expression(gen, builder, expr.then, expected_type, scope)
    then_exit = builder.block
    builder.branch(end_block)

    # without a context, the else arm adopts the then arm's type, so
    # literals on either side adapt to the declared one
    builder.position_at_end(else_block)
    else_value = emit_expression(gen, builder, expr.orelse,
                                 expected_type or then_value.type, scope)
    else_exit = builder.block
    builder.branch(end_block)

    if then_value.type != else_value.type:
        raise TypeError(f"ternary arms disagree: {then_value.type} vs {else_value.type}")

    builder.position_at_end(end_block)
    result = builder.phi(then_value.type, name="ternary")
    result.add_incoming(then_value, then_exit)
    result.add_incoming(else_value, else_exit)
    return result


def emit_logical(gen: CodeGenerator, builder: ir.IRBuilder, expr: BinaryOp, scope: dict):
    """
    Emit 'and'/'or' with short-circuit evaluation, yielding a bool.
    """
    left = emit_bool(gen, builder, expr.left, scope)

    func = builder.function
    rhs_block = func.append_basic_block(f"{expr.op}.rhs")
    end_block = func.append_basic_block(f"{expr.op}.end")

    # 'and' only evaluates the right side when the left is true; 'or' when false
    left_block = builder.block
    if expr.op == "and":
        builder.cbranch(left, rhs_block, end_block)
    else:
        builder.cbranch(left, end_block, rhs_block)

    builder.position_at_end(rhs_block)
    right = emit_bool(gen, builder, expr.right, scope)

    # the right side may have opened blocks of its own; the phi needs its exit block
    rhs_exit = builder.block
    builder.branch(end_block)

    # when short-circuited, the result is the left value that decided the branch
    builder.position_at_end(end_block)
    result = builder.phi(ir.IntType(1))
    result.add_incoming(ir.Constant(ir.IntType(1), expr.op == "or"), left_block)
    result.add_incoming(right, rhs_exit)
    return result


def emit_aggregate(gen: CodeGenerator, builder: ir.IRBuilder, expr: AggregateLiteral,
                   expected_type: ir.Type | None, scope: dict, field_names: list | None = None):
    """
    Emit an aggregate literal, filling the expected struct or array type field by field.

    A positional literal fills every field in order; a named one fills any
    subset in any order, leaving the rest zero-initialized. When the field
    Sie type names are known, each element is coerced to its field's type
    with the same widening rules as any other typed context.
    """
    # the literal takes its shape from context: an array's '{ptr, length}', say
    if not isinstance(expected_type, (ir.LiteralStructType, ir.IdentifiedStructType)):
        raise TypeError(f"aggregate literal needs a struct or array type, not {expected_type}")

    field_types = expected_type.elements

    if expr.names is not None:
        return emit_named_aggregate(gen, builder, expr, expected_type, scope, field_names)

    if len(expr.elements) != len(field_types):
        raise TypeError(f"aggregate literal has {len(expr.elements)} elements, "
                        f"expected {len(field_types)}")

    # build the value by inserting each element into an initially-undefined aggregate
    value = ir.Constant(expected_type, ir.Undefined)
    for index, (element, field_type) in enumerate(zip(expr.elements, field_types)):
        if field_names is not None:
            field = emit_coerced(gen, builder, element, field_names[index], scope)
        else:
            field = emit_expression(gen, builder, element, field_type, scope)

        value = builder.insert_value(value, field, index)

    return value


def aggregate_fields(gen: CodeGenerator, type_: ir.Type) -> list[str] | None:
    """
    The field names of an aggregate LLVM type, in order: a registered
    struct's own, or an array's synthetic 'data' and 'length'.
    """
    if isinstance(type_, ir.IdentifiedStructType):
        info = gen.structs.get(type_.name)
        if info is not None and info.fields:
            return [field.name for field in info.fields]

    if is_array_struct(type_):
        return ["data", "length"]

    return None


def emit_named_aggregate(gen: CodeGenerator, builder: ir.IRBuilder, expr: AggregateLiteral,
                         expected_type: ir.Type, scope: dict, field_names: list | None):
    """
    Emit a named aggregate literal over a zero-initialized base: each 'x =
    v' fills its field wherever it sits, and untouched fields stay zero.
    """
    names = aggregate_fields(gen, expected_type)
    if names is None:
        raise TypeError(f"named aggregate literal needs a struct or array "
                        f"type with known fields, not {expected_type}")

    index_of = {name: index for index, name in enumerate(names)}

    value = ir.Constant(expected_type, None)
    seen = set()
    for name, element in zip(expr.names, expr.elements):
        if name not in index_of:
            raise TypeError(f"aggregate literal names unknown field {name!r}")

        if name in seen:
            raise TypeError(f"aggregate literal sets field {name!r} more than once")

        seen.add(name)
        index = index_of[name]
        if field_names is not None:
            field = emit_coerced(gen, builder, element, field_names[index], scope)
        else:
            field = emit_expression(gen, builder, element,
                                    expected_type.elements[index], scope)

        value = builder.insert_value(value, field, index)

    return value


def emit_array(gen: CodeGenerator, builder: ir.IRBuilder, expr: ArrayLiteral,
              expected_type: ir.Type | None, scope: dict, element_name: str | None = None):
    """
    Emit an array literal, storing its elements into a backing array and
    wrapping a pointer to it with their count in the fat '{X*, u64}' array value.

    When the element's Sie type name is known, each element is coerced to it
    with the same widening rules as any other typed context.
    """
    # the literal takes its element type from context: an 'i32[]' target's
    # first field is a pointer to the element type it must build
    if not is_array_struct(expected_type):
        raise TypeError(f"array literal needs an array type, not {expected_type}")

    element_type = expected_type.elements[0].pointee

    # store each element into a stack-allocated backing array
    backing = entry_alloca(builder, ir.ArrayType(element_type, len(expr.elements)), "arr.lit")
    for index, element in enumerate(expr.elements):
        if element_name is not None:
            value = emit_coerced(gen, builder, element, element_name, scope)
        else:
            value = emit_expression(gen, builder, element, element_type, scope)

        slot = builder.gep(backing, [ir.Constant(ir.IntType(32), 0),
                                     ir.Constant(ir.IntType(32), index)])
        builder.store(value, slot)

    # decay the backing array to a pointer at its first element, and pair it
    # with the element count in the fat array value
    data = builder.gep(backing, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                       name="arr.lit.data")

    value = ir.Constant(expected_type, ir.Undefined)
    value = builder.insert_value(value, data, 0)
    value = builder.insert_value(value, ir.Constant(ir.IntType(64), len(expr.elements)), 1)
    return value


def emit_block_expr(gen: CodeGenerator, builder: ir.IRBuilder, expr: BlockExpr,
                    expected_type: ir.Type | None, scope: dict, target_name: str | None = None):
    """
    Emit a block used as a value: its statements run in a child scope, and
    an 'emit' inside stores the block's value and jumps past it.
    """
    # deferred import: statements and expressions are mutually recursive
    from siec.codegen.statements import emit_block

    if expected_type is None or isinstance(expected_type, ir.VoidType):
        raise TypeError("block expression needs a typed context to take its value from")

    slot = entry_alloca(builder, expected_type, "block.value")
    end_block = builder.function.append_basic_block("block.end")

    # the innermost target is what an 'emit' inside the body resolves to;
    # the defer depth marks which scopes an 'emit' leaves and must flush
    gen.emit_targets.append((slot, end_block, target_name, len(gen.defer_frames)))
    emit_block(gen, builder, expr.body, dict(scope))
    gen.emit_targets.pop()

    # every path must leave the block through an 'emit' (or a return)
    if not builder.block.is_terminated:
        raise TypeError("block expression must produce its value with 'emit'")

    builder.position_at_end(end_block)
    return builder.load(slot)


def emit_slice(gen: CodeGenerator, builder: ir.IRBuilder, expr: Slice,
               expected_type: ir.Type | None, scope: dict):
    """
    Emit an 'arr[from:to]' slice: a view over the base array's backing data,
    from 'from' (default 0) to 'to' (default the array's length).

    A slice keeps its base's type, so the context's expected type passes
    through — it's what gives a literal base its shape.
    """
    base = emit_expression(gen, builder, expr.base, expected_type, scope)
    if not is_array_struct(base.type):
        raise TypeError(f"cannot slice a value of type {base.type}")

    # the bounds are u64 element counts, like the array's own length
    start = (
        emit_coerced(gen, builder, expr.start, "u64", scope)
        if expr.start is not None
        else ir.Constant(ir.IntType(64), 0)
    )

    stop = (
        emit_coerced(gen, builder, expr.stop, "u64", scope)
        if expr.stop is not None
        else builder.extract_value(base, 1, name="slice.len")
    )

    # the view shares the backing data, offset to 'from', spanning 'to' - 'from'
    data = builder.gep(builder.extract_value(base, 0, name="slice.data"), [start])

    value = ir.Constant(base.type, ir.Undefined)
    value = builder.insert_value(value, data, 0)
    return builder.insert_value(value, builder.sub(stop, start), 1)


def emit_string(gen: CodeGenerator, builder: ir.IRBuilder, value: str):
    """
    Emit a string literal as a private global constant and return it as char*.
    """
    # null-terminate the bytes and size the constant array to fit
    data = value.encode() + b"\0"
    array_type = ir.ArrayType(ir.IntType(8), len(data))

    # store the data as a uniquely named module-level constant
    const = ir.GlobalVariable(gen.module, array_type, name=f".str.{gen.str_count}")
    const.global_constant = True
    const.linkage = "private"
    const.initializer = ir.Constant(array_type, bytearray(data))

    # hand it back decayed from [N x i8]* to a plain char*
    gen.str_count += 1
    return builder.bitcast(const, ir.PointerType(ir.IntType(8)))
