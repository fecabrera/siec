"""Registration and evaluation of enum declarations."""

from siec.ast import (BinaryOp, BoolLiteral, CharLiteral, EnumMember, IntLiteral,
                      Program, SizeOf, TypeId, UnaryOp, Var)
from siec.codegen.aliases import expand_alias, type_identity
from siec.codegen.errors import source_location
from siec.codegen.sizes import size_of
from siec.codegen.generator import CodeGenerator, EnumInfo, StructInfo
from siec.codegen.types import INTEGER_TYPES
from siec.diagnostics import ConstantEvaluationError


MAX_SHIFT = 127
MIN_CONSTANT = -(1 << 127)
MAX_CONSTANT = (1 << 128) - 1


def checked_result(value: int) -> int:
    """Keep constant integers within a representation Sie can provide."""
    if not MIN_CONSTANT <= value <= MAX_CONSTANT:
        raise ConstantEvaluationError(
            "integer overflow in constant expression: the result exceeds "
            "every Sie integer type")
    return value


def truncating_division(left: int, right: int) -> int:
    """LLVM/C signed division: truncate the quotient toward zero."""
    if right == 0:
        raise ConstantEvaluationError("division by zero in constant expression")

    quotient = abs(left) // abs(right)
    result = -quotient if (left < 0) != (right < 0) else quotient
    return checked_result(result)


def truncating_remainder(left: int, right: int) -> int:
    """LLVM/C signed remainder, whose sign follows the dividend."""
    return checked_result(left - truncating_division(left, right) * right)


def checked_shift(left: int, right: int, *, rightward: bool,
                  max_shift: int = MAX_SHIFT) -> int:
    """Reject shift counts that no Sie integer representation can execute."""
    if right < 0:
        raise ConstantEvaluationError(
            "shift count cannot be negative in constant expression")
    if right > max_shift:
        raise ConstantEvaluationError(
            f"shift count cannot exceed {max_shift} in constant expression")
    return checked_result(left >> right if rightward else left << right)


def checked_type_result(value: int, type_name: str) -> int:
    """Require a resolved constant value to fit its integer context."""
    bits = int(type_name[1:])
    low = -(1 << (bits - 1)) if type_name.startswith("i") else 0
    high = ((1 << (bits - 1)) - 1 if type_name.startswith("i")
            else (1 << bits) - 1)
    if not low <= value <= high:
        raise ConstantEvaluationError(
            f"constant value {value} does not fit {type_name}")
    return value


BINARY_OPS = {
    "+": lambda a, b: checked_result(a + b),
    "-": lambda a, b: checked_result(a - b),
    "*": lambda a, b: checked_result(a * b),
    "/": truncating_division,
    "%": truncating_remainder,
    "<<": lambda a, b: checked_shift(a, b, rightward=False),
    ">>": lambda a, b: checked_shift(a, b, rightward=True),
    "&": lambda a, b: checked_result(a & b),
    "|": lambda a, b: checked_result(a | b),
    "^": lambda a, b: checked_result(a ^ b),
    "==": lambda a, b: int(a == b),
    "!=": lambda a, b: int(a != b),
    "<": lambda a, b: int(a < b),
    "<=": lambda a, b: int(a <= b),
    ">": lambda a, b: int(a > b),
    ">=": lambda a, b: int(a >= b),
    "and": lambda a, b: int(bool(a) and bool(b)),
    "or": lambda a, b: int(bool(a) or bool(b)),
}


def collect_enums(gen: CodeGenerator, program: Program) -> None:
    """
    Add enum and member identities to the declaration inventory.

    Collection deliberately leaves backing types and member expressions
    untouched. ``resolve_enums`` consumes the complete collected inventory.
    """
    if gen.declaration_inventory_complete:
        raise RuntimeError(
            "enum collection continued after its inventory was frozen")

    for enum in program.enums:
        declaration_id = id(enum)
        if declaration_id in gen.collected_enums:
            continue

        with source_location(line=enum.line, file=enum.file):
            owner = type_identity(gen, enum.name)
            if owner == "builtin":
                raise TypeError(f"{enum.name!r} is a builtin type: "
                                "declarations cannot take its name")
            if owner is not None:
                raise TypeError(f"type {enum.name!r} is declared more than once")

            info = EnumInfo(enum.type, {})
            gen.enums[enum.name] = info

            for index, member in enumerate(enum.members):
                key = (enum.name, member.name)
                if key in gen.enum_member_declarations:
                    raise TypeError(f"enum {enum.name!r} declares member "
                                    f"{member.name!r} more than once")

                gen.enum_member_declarations[key] = (enum, member, index)

            gen.collected_enums.add(declaration_id)
            gen.enum_declarations.append(enum)


def resolve_enums(gen: CodeGenerator) -> None:
    """
    Resolve every collected enum backing type and member dependency.

    Automatic values start at 0; an explicit value resets the counter, and
    following members keep counting from there. All backing types resolve
    before any member expression so dependencies are declaration-order
    independent.
    """

    # Resolve every backing type only after all enum names are available.
    for enum in gen.enum_declarations:
        if id(enum) in gen.resolved_enums:
            continue

        with source_location(line=enum.line, file=enum.file):
            gen.current_file = enum.file
            enum.type = expand_alias(gen, enum.type)
            if enum.type not in INTEGER_TYPES:
                raise TypeError(f"enum {enum.name!r} needs an integer backing "
                                f"type, not {enum.type!r}")

            info = gen.enums[enum.name]
            info.backing = enum.type
            gen.structs[enum.name] = StructInfo(
                None,
                [],
                backing=enum.type,
            )

    active = []

    def resolve_member(expr: EnumMember) -> int:
        name = resolve_enum(gen, expr.enum)
        info = gen.enums.get(name)
        if info is None:
            raise NameError(f"undefined enum {expr.enum!r}")

        key = (name, expr.member)
        if key not in gen.enum_member_declarations:
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

        enum, member, index = gen.enum_member_declarations[key]
        active.append(key)
        previous_file = gen.current_file
        gen.current_file = enum.file
        try:
            with source_location(line=member.line, file=enum.file):
                if member.value is not None:
                    value = evaluate(
                        gen,
                        member.value,
                        resolve_member,
                        integer_type=enum.type,
                    )
                elif index == 0:
                    value = 0
                else:
                    previous = enum.members[index - 1]
                    value = BINARY_OPS["+"](
                        resolve_key((enum.name, previous.name)), 1)
                value = checked_type_result(value, enum.type)
        finally:
            gen.current_file = previous_file
            active.pop()

        info.members[member_name] = value
        return value

    # Resolve every member even when no expression uses it, reporting invalid
    # references and cycles at its declaration.
    for enum in gen.enum_declarations:
        if id(enum) in gen.resolved_enums:
            continue

        for member in enum.members:
            resolve_key((enum.name, member.name))

        gen.resolved_enums.add(id(enum))


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

        # A module may export a public alias for a private or low-level enum.
        # Enum members carry the canonical target type just like values in
        # annotations do, so calls see one type on both sides.
        return expand_alias(gen, member, checked=False)

    bound = gen.member_bindings.get((gen.current_file, name))
    if bound is not None and bound != name:
        canonical = expand_alias(gen, bound, checked=False)
        if canonical in gen.enums:
            return canonical

    canonical = expand_alias(gen, name)
    if canonical in gen.enums and not gen.ungated_types and not gen.sees(name):
        raise TypeError(f"unknown type {name!r}")

    return canonical


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


def evaluate(gen: CodeGenerator, expr, enum_resolver=None,
             integer_type: str | None = None) -> int:
    """
    Evaluate a constant integer expression at compile time: literals,
    integer operators, enum members, and '@const' references.
    """
    if isinstance(expr, IntLiteral):
        return checked_result(expr.value)

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
            return evaluate(gen, const.value, enum_resolver, integer_type)

    if isinstance(expr, UnaryOp) and expr.op in ("-", "~", "not"):
        value = evaluate(gen, expr.operand, enum_resolver, integer_type)
        if expr.op == "not":
            return int(not value)

        return checked_result(-value if expr.op == "-" else ~value)

    if isinstance(expr, BinaryOp) and expr.op in BINARY_OPS:
        left = evaluate(gen, expr.left, enum_resolver, integer_type)

        # Match generated control flow: an unneeded right operand must not
        # trigger an invalid operation or resolve a missing constant.
        if expr.op == "and" and not left:
            return 0
        if expr.op == "or" and left:
            return 1

        right = evaluate(gen, expr.right, enum_resolver, integer_type)
        if expr.op in ("<<", ">>") and integer_type is not None:
            return checked_shift(
                left,
                right,
                rightward=expr.op == ">>",
                max_shift=int(integer_type[1:]) - 1,
            )
        return BINARY_OPS[expr.op](left, right)

    raise TypeError("value must be a constant integer expression")
