"""Emission of expressions: literals, variables, and calls."""

from llvmlite import ir

from siec.ast import (
    AggregateLiteral,
    ArrayLiteral,
    BinaryOp,
    BlockExpr,
    BoolLiteral,
    Call,
    Cast,
    Expr,
    Field,
    FloatLiteral,
    Index,
    IntLiteral,
    Member,
    Slice,
    StrLiteral,
    UnaryOp,
    Var,
)
from siec.codegen.generator import CodeGenerator, StructInfo, entry_alloca
from siec.codegen.types import (
    fn_type_parts,
    is_aliasing,
    is_array_struct,
    is_const,
    is_reference,
    resolve_type,
    sized_array,
    strip_const,
    strip_reference,
    type_signedness,
)

# arithmetic and bitwise operators and the IRBuilder method emitting each;
# division, remainder, and right shift change instruction on unsigned
# operands, and arithmetic changes wholesale on floats
ARITHMETIC = {"+": "add", "-": "sub", "*": "mul", "/": "sdiv", "%": "srem",
              "<<": "shl", ">>": "ashr", "&": "and_", "|": "or_", "^": "xor"}
UNSIGNED_ARITHMETIC = {"/": "udiv", "%": "urem", ">>": "lshr"}
FLOAT_ARITHMETIC = {"+": "fadd", "-": "fsub", "*": "fmul", "/": "fdiv", "%": "frem"}


def is_float(type_: ir.Type) -> bool:
    """
    Whether an LLVM type is a floating-point scalar.
    """
    return isinstance(type_, (ir.FloatType, ir.DoubleType))


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

    if isinstance(expr, AggregateLiteral):
        return emit_aggregate(gen, builder, expr, expected_type, scope)

    if isinstance(expr, BlockExpr):
        return emit_block_expr(gen, builder, expr, expected_type, scope)

    if isinstance(expr, ArrayLiteral):
        return emit_array(gen, builder, expr, expected_type, scope)

    if isinstance(expr, Var):
        # variables load their current value from their stack slot
        if expr.name in scope:
            return builder.load(scope[expr.name].slot, name=expr.name)

        # a constant substitutes its value expression in place, coerced to
        # its annotated type when it has one, adapting like a literal otherwise
        const = gen.constants.get(expr.name)
        if const is not None:
            if const.type is not None:
                return emit_coerced(gen, builder, const.value, const.type, scope)

            return emit_expression(gen, builder, const.value, expected_type, scope)

        # a bare function name is a reference to that function
        func = gen.module.globals.get(expr.name)
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
        return builder.load(builder.gep(base, [index]))

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


def expr_sie_type(gen: CodeGenerator, expr: Expr, scope: dict) -> str | None:
    """
    Infer the Sie type name of an expression; None when it has no fixed one.
    """
    # variables and calls carry their declared Sie type; a bare function
    # name carries the canonical fn type of its signature; a '&T'
    # reference parameter reads as the T it aliases
    if isinstance(expr, Var):
        if expr.name in scope:
            return strip_reference(scope[expr.name].type)

        # a constant carries its annotation; unannotated, it adapts like
        # its value expression written in place
        const = gen.constants.get(expr.name)
        if const is not None:
            return const.type if const.type is not None else expr_sie_type(
                gen, const.value, scope)

        if expr.name in gen.param_types:
            params = ",".join(gen.param_types[expr.name])
            ret = gen.return_types.get(expr.name)
            return f"fn({params})" + (f"->{ret}" if ret else "")

        return None

    if isinstance(expr, Call):
        # a call through a function reference yields the reference's return type
        if expr.name in scope and strip_const(scope[expr.name].type).startswith("fn("):
            return fn_type_parts(strip_const(scope[expr.name].type))[1]

        return gen.return_types.get(expr.name)

    # a cast produces its target type
    if isinstance(expr, Cast):
        return expr.type

    # a member access yields the field's type; an aliasing field (a pointer
    # or array) keeps a const base's contract
    if isinstance(expr, Member):
        base_name = expr_sie_type(gen, expr.base, scope)
        info = type_info(gen, base_name)
        if info is None:
            return None

        field_type = info.field(expr.field)[1]
        if is_const(base_name) and is_aliasing(field_type) and not is_const(field_type):
            return f"const {field_type}"

        return field_type

    # indexing yields the element type, one '[]' or '*' shorter; an aliasing
    # element keeps a const base's contract
    if isinstance(expr, Index):
        base = expr_sie_type(gen, expr.base, scope)
        if base is None:
            return None

        stripped = strip_const(base)
        element = stripped[:-2] if stripped.endswith("[]") else stripped.removesuffix("*")
        if is_const(base) and is_aliasing(element):
            return f"const {element}"

        return element

    # a slice is a view with its base's array type
    if isinstance(expr, Slice):
        return expr_sie_type(gen, expr.base, scope)

    # '&' yields a pointer to its operand's type
    if isinstance(expr, UnaryOp) and expr.op == "&":
        operand = expr_sie_type(gen, expr.operand, scope)
        return f"{operand}*" if operand is not None else None

    return None


COMPARISONS = {"<", ">", "<=", ">=", "==", "!="}


def infer_type(gen: CodeGenerator, expr: Expr, scope: dict) -> str | None:
    """
    Infer the Sie type an unannotated 'let' adopts from its initializer;
    None when the expression doesn't pin one down.
    """
    # named values, calls, casts, members, and the rest carry declared types;
    # a copy of a non-aliasing const value is an independent, mutable value
    declared = expr_sie_type(gen, expr, scope)
    if declared is not None:
        if is_const(declared) and not is_aliasing(strip_const(declared)):
            return strip_const(declared)

        return declared

    # literals default like they do in any untyped context
    if isinstance(expr, IntLiteral):
        return "i32"

    if isinstance(expr, FloatLiteral):
        return "f64"

    if isinstance(expr, StrLiteral):
        return "char*"

    if isinstance(expr, BoolLiteral):
        return "bool"

    # 'not' yields a bool; '-' and '~' keep their operand's type
    if isinstance(expr, UnaryOp):
        return "bool" if expr.op == "not" else infer_type(gen, expr.operand, scope)

    if isinstance(expr, BinaryOp):
        if expr.op in ("and", "or") or expr.op in COMPARISONS:
            return "bool"

        # arithmetic keeps its operands' type; a declared operand wins, so a
        # literal beside it adapts as in any typed context
        return (expr_sie_type(gen, expr.left, scope)
                or expr_sie_type(gen, expr.right, scope)
                or infer_type(gen, expr.left, scope)
                or infer_type(gen, expr.right, scope))

    return None


def signedness(gen: CodeGenerator, expr: Expr, scope: dict) -> str | None:
    """
    Infer the signedness of an expression; None when it has no fixed one.
    """
    # named values take the signedness of their declared Sie type
    if isinstance(expr, (Var, Call, Member, Index)):
        return type_signedness(expr_sie_type(gen, expr, scope))

    # arithmetic keeps the signedness of its operands; literals adapt to either
    if isinstance(expr, UnaryOp) and expr.op in ("-", "~"):
        return signedness(gen, expr.operand, scope)

    if isinstance(expr, BinaryOp) and (expr.op in ARITHMETIC or expr.op == "**"):
        return signedness(gen, expr.left, scope) or signedness(gen, expr.right, scope)

    return None


def numeric_class(type_name: str | None) -> tuple[str, int] | None:
    """
    Classify a scalar numeric type name as its ('i'|'u'|'f', width), else None.
    """
    type_name = strip_const(type_name)
    if type_name and type_name[0] in "iuf" and type_name[1:].isdigit():
        return type_name[0], int(type_name[1:])

    return None


def value_class(gen: CodeGenerator, value: ir.Value, expr: Expr,
                scope: dict) -> tuple[str, int] | None:
    """
    Classify an emitted value's numeric prefix and width, from its type and signedness.
    """
    # prefer the declared type name when the expression has one
    declared = numeric_class(expr_sie_type(gen, expr, scope))
    if declared is not None:
        return declared

    # otherwise read the width from the LLVM type and the prefix from signedness
    if isinstance(value.type, ir.FloatType):
        return "f", 32

    if isinstance(value.type, ir.DoubleType):
        return "f", 64

    if isinstance(value.type, ir.IntType):
        prefix = {"signed": "i", "unsigned": "u"}.get(signedness(gen, expr, scope))
        return (prefix, value.type.width) if prefix is not None else None

    return None


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


def emit_cast(gen: CodeGenerator, builder: ir.IRBuilder, expr: Cast, scope: dict):
    """
    Emit an explicit numeric cast, choosing the LLVM conversion the prefixes and widths call for.

    The 'const' contract survives casts: an aliasing const value (a pointer
    or array) never becomes mutable, not even explicitly.
    """
    operand_name = expr_sie_type(gen, expr.operand, scope)
    if (is_const(operand_name) and not is_const(expr.type)
            and is_aliasing(strip_const(operand_name))):
        raise TypeError(f"cannot cast away 'const': {operand_name!r} to {expr.type!r}")

    target = numeric_class(expr.type)
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
    mutable one is expected — only an explicit cast sheds the contract.
    """
    source_name = expr_sie_type(gen, expr, scope)
    if (target_name is not None and is_const(source_name) and not is_const(target_name)
            and is_aliasing(strip_const(source_name))):
        raise TypeError(f"cannot use a {source_name!r} value where a mutable "
                        f"{target_name!r} is expected")

    target_name = strip_const(target_name)
    target_type = resolve_type(target_name, gen.structs)

    # an aggregate literal coerces each element to its field's type instead
    if isinstance(expr, AggregateLiteral):
        info = type_info(gen, target_name)
        field_names = [f.type for f in info.fields] if info is not None else None
        return emit_aggregate(gen, builder, expr, target_type, scope, field_names)

    # a block expression coerces each emitted value to the target instead
    if isinstance(expr, BlockExpr):
        return emit_block_expr(gen, builder, expr, target_type, scope, target_name)

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


def type_info(gen: CodeGenerator, type_name: str | None) -> StructInfo | None:
    """
    Return the fields of a struct or array type name, or None for other types.
    """
    # a 'const' base has the same fields as its represented type
    type_name = strip_const(type_name)

    # a sized 'X[N]' carries the same fields as the 'X[]' it declares
    if (sized := sized_array(type_name)) is not None:
        type_name = sized[0]

    # an 'X[]' array exposes two synthetic fields: 'data' (X*) and 'length' (u64)
    if type_name and type_name.endswith("[]"):
        element = type_name[:-2]
        fields = [Field("data", f"{element}*"), Field("length", "u64")]
        return StructInfo(resolve_type(type_name, gen.structs), fields)

    return gen.structs.get(type_name)


def member_field(gen: CodeGenerator, expr: Member, scope: dict) -> tuple[int, str]:
    """
    Resolve a member access to its field index and Sie type, checking the base has fields.
    """
    base_type = expr_sie_type(gen, expr.base, scope)
    info = type_info(gen, base_type)
    if info is None:
        raise TypeError(f"cannot access field {expr.field!r} on non-struct type {base_type}")

    return info.field(expr.field)


def emit_lvalue(gen: CodeGenerator, builder: ir.IRBuilder, expr: Expr, scope: dict):
    """
    Emit the address of an assignable expression: a variable, a struct/array
    field, or a pointer-indexed element.
    """
    if isinstance(expr, Var):
        if expr.name not in scope:
            raise NameError(f"undefined variable {expr.name!r}")

        return scope[expr.name].slot

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


def check_signedness(gen: CodeGenerator, expr: BinaryOp, scope: dict) -> str | None:
    """
    Reject an operation mixing a signed and an unsigned operand,
    returning the signedness the operands agree on.
    """
    left = signedness(gen, expr.left, scope)
    right = signedness(gen, expr.right, scope)

    if left is not None and right is not None and left != right:
        raise TypeError(f"cannot apply {expr.op!r} to {left} and {right} operands")

    return left or right


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

    When the field Sie type names are known, each element is coerced to its field's
    type with the same widening rules as any other typed context.
    """
    # the literal takes its shape from context: an array's '{ptr, length}', say
    if not isinstance(expected_type, (ir.LiteralStructType, ir.IdentifiedStructType)):
        raise TypeError(f"aggregate literal needs a struct or array type, not {expected_type}")

    field_types = expected_type.elements
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


def emit_call(gen: CodeGenerator, builder: ir.IRBuilder, call: Call, scope: dict):
    """
    Emit a call to a declared function, checking the argument count.
    """
    # a variable holding a function reference is called through its value
    if call.name in scope:
        return emit_indirect_call(gen, builder, call, scope)

    # look up the callee among the module's declared functions
    func = gen.module.globals.get(call.name)
    if not isinstance(func, ir.Function):
        raise NameError(f"undefined function {call.name!r}")

    # check arity, letting varargs functions take extra arguments
    param_types = func.function_type.args

    if len(call.args) < len(param_types):
        raise TypeError(f"too few arguments to function {call.name!r}")

    if len(call.args) > len(param_types) and not func.function_type.var_arg:
        raise TypeError(f"too many arguments to function {call.name!r}")

    # coerce each argument to its parameter's Sie type; vararg extras pass
    # as-is, except an f32, which promotes to f64 like C's default promotions
    sie_params = gen.param_types.get(call.name, [])

    args = []
    for i, arg in enumerate(call.args):
        if i < len(sie_params):
            args.append(emit_argument(gen, builder, arg, sie_params[i], scope))
        else:
            value = emit_expression(gen, builder, arg, None, scope)
            if isinstance(value.type, ir.FloatType):
                value = builder.fpext(value, ir.DoubleType())

            args.append(value)

    return builder.call(func, args)


def emit_argument(gen: CodeGenerator, builder: ir.IRBuilder, arg: Expr,
                  param_name: str, scope: dict):
    """
    Emit one call argument: coerced to the parameter's type, or, for a '&T'
    reference parameter, the argument's own address, passed implicitly.
    """
    if not is_reference(param_name):
        return emit_coerced(gen, builder, arg, param_name, scope)

    referenced = strip_reference(param_name)
    arg_name = expr_sie_type(gen, arg, scope)

    if arg_name is not None:
        # the callee aliases the storage itself, so the types must match
        # exactly: no widening can happen in place
        if strip_const(arg_name) != strip_const(referenced):
            raise TypeError(f"cannot bind a {arg_name!r} value to a "
                            f"{param_name!r} parameter")

        # a const value only binds to a 'const &T'
        if is_const(arg_name) and not is_const(referenced):
            raise TypeError(f"cannot bind a {arg_name!r} value to a mutable "
                            f"{param_name!r} parameter")

    try:
        return emit_lvalue(gen, builder, arg, scope)
    except TypeError:
        raise TypeError(f"a {param_name!r} parameter needs an "
                        "assignable argument") from None


def emit_indirect_call(gen: CodeGenerator, builder: ir.IRBuilder, call: Call, scope: dict):
    """
    Emit a call through a function reference held in a variable.
    """
    var = scope[call.name]
    var_type = strip_const(var.type)
    if not var_type.startswith("fn(") or fn_type_parts(var_type)[2]:
        raise TypeError(f"cannot call non-function variable {call.name!r}")

    sie_params = fn_type_parts(var_type)[0]
    if len(call.args) != len(sie_params):
        raise TypeError(f"function reference {call.name!r} takes "
                        f"{len(sie_params)} arguments, got {len(call.args)}")

    callee = builder.load(var.slot, name=call.name)
    args = [emit_argument(gen, builder, arg, sie_params[i], scope)
            for i, arg in enumerate(call.args)]

    return builder.call(callee, args)
