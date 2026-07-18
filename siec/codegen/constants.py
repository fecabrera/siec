"""Registration and validation of '@const' declarations."""

from siec.ast import (
    BinaryOp,
    BoolLiteral,
    Cast,
    EnumMember,
    Expr,
    FloatLiteral,
    IntLiteral,
    Program,
    StrLiteral,
    UnaryOp,
    Var,
)
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator

LITERALS = (IntLiteral, FloatLiteral, StrLiteral, BoolLiteral)


def register_constants(gen: CodeGenerator, program: Program) -> None:
    """
    Register every '@const', then check each value is a constant expression.
    """
    for const in program.consts:
        with source_location(line=const.line, file=const.file):
            if const.name in gen.constants:
                raise TypeError(f"constant {const.name!r} is declared more than once")

            gen.constants[const.name] = const

    # validate after registration so constants may reference one another
    # regardless of declaration order
    for const in program.consts:
        with source_location(line=const.line, file=const.file):
            check_constant(gen, const.value, [const.name])


def check_constant(gen: CodeGenerator, expr: Expr, chain: list[str]) -> None:
    """
    Reject anything but literals, operators, casts, and other constants in a
    constant's value, following references to catch cycles.
    """
    if isinstance(expr, LITERALS):
        return

    # an enum member is a named integer constant; its existence is
    # checked where the constant is used
    if isinstance(expr, EnumMember):
        return

    if isinstance(expr, Var):
        other = gen.constants.get(expr.name)
        if other is None:
            raise TypeError(f"constant {chain[0]!r} references "
                            f"non-constant {expr.name!r}")

        if expr.name in chain:
            cycle = " -> ".join([*chain, expr.name])
            raise TypeError(f"constant cycle: {cycle}")

        check_constant(gen, other.value, [*chain, expr.name])
        return

    if isinstance(expr, UnaryOp):
        check_constant(gen, expr.operand, chain)
        return

    if isinstance(expr, BinaryOp):
        check_constant(gen, expr.left, chain)
        check_constant(gen, expr.right, chain)
        return

    if isinstance(expr, Cast):
        check_constant(gen, expr.operand, chain)
        return

    raise TypeError(f"constant {chain[0]!r} must be a constant expression")
