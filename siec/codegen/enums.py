"""Registration and evaluation of enum declarations."""

from siec.ast import (BinaryOp, BoolLiteral, CharLiteral, EnumMember, IntLiteral,
                      Program, SizeOf, UnaryOp, Var)
from siec.codegen.aliases import expand_alias
from siec.codegen.errors import source_location
from siec.codegen.sizes import size_of
from siec.codegen.generator import CodeGenerator, EnumInfo, StructInfo
from siec.codegen.types import resolve_type

INTEGER_TYPES = {"i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64"}

BINARY_OPS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a // b,
    "%": lambda a, b: a % b,
    "<<": lambda a, b: a << b,
    ">>": lambda a, b: a >> b,
    "&": lambda a, b: a & b,
    "|": lambda a, b: a | b,
    "^": lambda a, b: a ^ b,
    "==": lambda a, b: int(a == b),
    "!=": lambda a, b: int(a != b),
    "<": lambda a, b: int(a < b),
    "<=": lambda a, b: int(a <= b),
    ">": lambda a, b: int(a > b),
    ">=": lambda a, b: int(a >= b),
    "and": lambda a, b: int(bool(a) and bool(b)),
    "or": lambda a, b: int(bool(a) or bool(b)),
}


def register_enums(gen: CodeGenerator, program: Program) -> None:
    """
    Register every enum, evaluating its members to integer constants.

    Automatic values start at 1; an explicit '= <value>' resets the counter,
    and the following members keep counting from there. A member's value may
    reference members already declared, in this or an earlier enum.
    """
    for enum in program.enums:
        with source_location(line=enum.line, file=enum.file):
            if enum.name in gen.enums or enum.name in gen.structs:
                raise TypeError(f"type {enum.name!r} is declared more than once")

            gen.current_file = enum.file
            enum.type = expand_alias(gen, enum.type)
            if enum.type not in INTEGER_TYPES:
                raise TypeError(f"enum {enum.name!r} needs an integer backing "
                                f"type, not {enum.type!r}")

            # register before evaluating so members can reference earlier
            # ones of the same enum; the enum name also resolves as a type,
            # represented by its backing type
            info = EnumInfo(enum.type, {})
            gen.enums[enum.name] = info
            gen.structs[enum.name] = StructInfo(resolve_type(enum.type), [])

            counter = 1
            for member in enum.members:
                if member.name in info.members:
                    raise TypeError(f"enum {enum.name!r} declares member "
                                    f"{member.name!r} more than once")

                if member.value is not None:
                    counter = evaluate(gen, member.value)

                info.members[member.name] = counter
                counter += 1


def member_value(gen: CodeGenerator, expr: EnumMember) -> int:
    """
    Look up an 'A::member' reference, checking the enum and member exist.
    """
    info = gen.enums.get(expr.enum)
    if info is None:
        raise NameError(f"undefined enum {expr.enum!r}")

    if expr.member not in info.members:
        raise TypeError(f"enum {expr.enum!r} has no member {expr.member!r}")

    return info.members[expr.member]


def evaluate_size(gen: CodeGenerator, text: str) -> int:
    """
    Evaluate a sized array's '[N]' text: a constant integer expression
    kept as tokens by the parser, required to be positive.
    """
    # deferred import: the parser package doesn't depend on codegen
    from siec.lexer import lex
    from siec.parser.expressions import parse_expression
    from siec.parser.stream import TokenStream

    size = evaluate(gen, parse_expression(TokenStream(lex(text))))
    if size <= 0:
        raise TypeError(f"array size must be positive, not {size}")

    return size


def evaluate(gen: CodeGenerator, expr) -> int:
    """
    Evaluate a constant integer expression at compile time: literals,
    integer operators, enum members, and '@const' references.
    """
    if isinstance(expr, IntLiteral):
        return expr.value

    if isinstance(expr, BoolLiteral):
        return int(expr.value)

    # a char literal evaluates to its byte value
    if isinstance(expr, CharLiteral):
        return expr.value.encode()[0]

    if isinstance(expr, EnumMember):
        return member_value(gen, expr)

    # a sizeof is a compile-time byte count; only type names resolve here,
    # constant contexts having no variables in scope
    if isinstance(expr, SizeOf):
        return size_of(gen, expr.name)

    if isinstance(expr, Var):
        const = gen.constants.get(expr.name)
        if const is None:
            raise TypeError(f"{expr.name!r} is not a compile-time constant")

        return evaluate(gen, const.value)

    if isinstance(expr, UnaryOp) and expr.op in ("-", "~", "not"):
        value = evaluate(gen, expr.operand)
        if expr.op == "not":
            return int(not value)

        return -value if expr.op == "-" else ~value

    if isinstance(expr, BinaryOp) and expr.op in BINARY_OPS:
        return BINARY_OPS[expr.op](evaluate(gen, expr.left), evaluate(gen, expr.right))

    raise TypeError("value must be a constant integer expression")
