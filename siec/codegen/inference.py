"""Type analysis of expressions: Sie types, signedness, and numeric classes.

Everything here answers questions about expressions — what type is this,
how does it classify — without emitting any IR.
"""

from llvmlite import ir

from siec.ast import (
    BinaryOp,
    BoolLiteral,
    Call,
    Cast,
    EnumMember,
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
from siec.codegen.generator import CodeGenerator, StructInfo
from siec.codegen.types import (
    fn_type_parts,
    is_aliasing,
    is_const,
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

COMPARISONS = {"<", ">", "<=", ">=", "==", "!="}


def is_float(type_: ir.Type) -> bool:
    """
    Whether an LLVM type is a floating-point scalar.
    """
    return isinstance(type_, (ir.FloatType, ir.DoubleType))


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

        # a global carries its declared type
        if expr.name in gen.globals:
            return gen.globals[expr.name]

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

    # 'A::member' carries its enum's type name
    if isinstance(expr, EnumMember):
        return expr.enum

    return None


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


def enum_backing(gen: CodeGenerator, name: str | None) -> str | None:
    """
    Map an enum type name to its backing numeric type name, keeping any
    'const' marking; other names pass through unchanged.
    """
    info = gen.enums.get(strip_const(name)) if name is not None else None
    if info is None:
        return name

    return f"const {info.backing}" if is_const(name) else info.backing


def signedness(gen: CodeGenerator, expr: Expr, scope: dict) -> str | None:
    """
    Infer the signedness of an expression; None when it has no fixed one.
    """
    # named values take the signedness of their declared Sie type; an
    # enum-typed value takes its backing type's
    if isinstance(expr, (Var, Call, Member, Index, EnumMember)):
        return type_signedness(enum_backing(gen, expr_sie_type(gen, expr, scope)))

    # arithmetic keeps the signedness of its operands; literals adapt to either
    if isinstance(expr, UnaryOp) and expr.op in ("-", "~"):
        return signedness(gen, expr.operand, scope)

    if isinstance(expr, BinaryOp) and (expr.op in ARITHMETIC or expr.op == "**"):
        return signedness(gen, expr.left, scope) or signedness(gen, expr.right, scope)

    return None


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
    # prefer the declared type name when the expression has one; an
    # enum-typed value classifies as its backing type
    declared = numeric_class(enum_backing(gen, expr_sie_type(gen, expr, scope)))
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
