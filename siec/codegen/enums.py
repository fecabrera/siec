"""Registration and evaluation of enum declarations."""

from siec.ast import (BinaryOp, BoolLiteral, CharLiteral, EnumMember, IntLiteral,
                      Program, SizeOf, TypeId, UnaryOp, Var)
from siec.codegen.aliases import expand_alias, type_identity
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
    Register every enum and member, then evaluate the members.

    Automatic values start at 1; an explicit '= <value>' resets the counter,
    and the following members keep counting from there. Member values resolve
    through the complete inventory, so they may reference any enum regardless
    of declaration order.
    """
    declarations = {}

    # Collect every enum and member identity before resolving a backing type
    # or value. Evaluation can then follow references in either direction.
    for enum in program.enums:
        with source_location(line=enum.line, file=enum.file):
            if type_identity(gen, enum.name) is not None:
                raise TypeError(f"type {enum.name!r} is declared more than once")

            info = EnumInfo(enum.type, {})
            gen.enums[enum.name] = info

            for index, member in enumerate(enum.members):
                key = (enum.name, member.name)
                if key in declarations:
                    raise TypeError(f"enum {enum.name!r} declares member "
                                    f"{member.name!r} more than once")

                declarations[key] = (enum, member, index)

    # Resolve every backing type only after all enum names are available.
    for enum in program.enums:
        with source_location(line=enum.line, file=enum.file):
            gen.current_file = enum.file
            enum.type = expand_alias(gen, enum.type)
            if enum.type not in INTEGER_TYPES:
                raise TypeError(f"enum {enum.name!r} needs an integer backing "
                                f"type, not {enum.type!r}")

            info = gen.enums[enum.name]
            info.backing = enum.type
            gen.structs[enum.name] = StructInfo(resolve_type(enum.type), [])

    active = []

    def resolve_member(expr: EnumMember) -> int:
        name = resolve_enum(gen, expr.enum)
        info = gen.enums.get(name)
        if info is None:
            raise NameError(f"undefined enum {expr.enum!r}")

        key = (name, expr.member)
        if key not in declarations:
            raise TypeError(f"enum {expr.enum!r} has no member "
                            f"{expr.member!r}")

        return resolve_key(key)

    def resolve_key(key: tuple[str, str]) -> int:
        enum_name, member_name = key
        info = gen.enums[enum_name]
        if member_name in info.members:
            return info.members[member_name]

        if key in active:
            start = active.index(key)
            cycle = " -> ".join(
                f"{name}::{member}" for name, member in [*active[start:], key]
            )
            raise TypeError(f"enum member cycle: {cycle}")

        enum, member, index = declarations[key]
        active.append(key)
        previous_file = gen.current_file
        gen.current_file = enum.file
        try:
            with source_location(line=member.line, file=enum.file):
                if member.value is not None:
                    value = evaluate(gen, member.value, resolve_member)
                elif index == 0:
                    value = 1
                else:
                    previous = enum.members[index - 1]
                    value = resolve_key((enum.name, previous.name)) + 1
        finally:
            gen.current_file = previous_file
            active.pop()

        info.members[member_name] = value
        return value

    # Resolve every member even when no expression uses it, reporting invalid
    # references and cycles at its declaration.
    for enum in program.enums:
        for member in enum.members:
            resolve_key((enum.name, member.name))


def resolve_enum(gen: CodeGenerator, name: str) -> str:
    """
    The registered name an enum spelling reaches: a dotted one through
    the file's module bindings, a member-imported one through its
    binding, and a plain one held to the file's view - like any type.
    """
    if "." in name:
        member = gen.resolve_qualified(name.split("."))
        if member is None:
            raise NameError(f"undefined enum {name!r}")

        return member

    bound = gen.member_bindings.get((gen.current_file, name))
    if bound is not None and bound != name and bound in gen.enums:
        return bound

    if name in gen.enums and not gen.ungated_types and not gen.sees(name):
        raise TypeError(f"unknown type {name!r}")

    return name


def member_value(gen: CodeGenerator, expr: EnumMember) -> int:
    """
    Look up an 'A::member' reference, checking the enum and member exist.
    """
    info = gen.enums.get(resolve_enum(gen, expr.enum))
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


def evaluate(gen: CodeGenerator, expr, enum_resolver=None) -> int:
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
        if enum_resolver is not None:
            return enum_resolver(expr)

        return member_value(gen, expr)

    # a '@sizeof' is a compile-time byte count; only type names resolve here,
    # constant contexts having no variables in scope; a '@typeid' hashes
    # the same way
    if isinstance(expr, SizeOf):
        return size_of(gen, expr.name)

    if isinstance(expr, TypeId):
        # deferred import: expressions and enums are mutually recursive
        from siec.codegen.expressions import fnv1a, typename_of

        return fnv1a(typename_of(gen, expr.name, {}))

    if isinstance(expr, Var):
        from siec.codegen.constants import constant_view, find_constant

        const = find_constant(gen, expr.name, getattr(expr, "module_file", None))
        if const is None:
            raise TypeError(f"{expr.name!r} is not a compile-time constant")

        with constant_view(gen, const):
            return evaluate(gen, const.value, enum_resolver)

    if isinstance(expr, UnaryOp) and expr.op in ("-", "~", "not"):
        value = evaluate(gen, expr.operand, enum_resolver)
        if expr.op == "not":
            return int(not value)

        return -value if expr.op == "-" else ~value

    if isinstance(expr, BinaryOp) and expr.op in BINARY_OPS:
        left = evaluate(gen, expr.left, enum_resolver)
        right = evaluate(gen, expr.right, enum_resolver)
        return BINARY_OPS[expr.op](left, right)

    raise TypeError("value must be a constant integer expression")
