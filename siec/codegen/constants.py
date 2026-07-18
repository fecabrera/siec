"""Registration and validation of '@const' declarations."""

from siec.ast import (
    BinaryOp,
    BoolLiteral,
    Cast,
    CharLiteral,
    Const,
    EnumMember,
    Expr,
    FloatLiteral,
    IntLiteral,
    NullLiteral,
    Program,
    SizeOf,
    StrLiteral,
    UnaryOp,
    Var,
)
from siec.codegen.aliases import expand_alias
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator

LITERALS = (IntLiteral, FloatLiteral, StrLiteral, BoolLiteral, CharLiteral,
            NullLiteral)

# the OS and architecture families a target triple classifies into,
# defined as '@const's in every program; 0 marks the unknowns
TARGET_CONSTANTS = {
    "OS_UNKNOWN": 0,
    "OS_DARWIN": 1,
    "OS_LINUX": 2,
    "OS_WINDOWS": 3,
    "OS_NONE": 4,
    "ARCH_UNKNOWN": 0,
    "ARCH_X86_64": 1,
    "ARCH_AARCH64": 2,
    "ARCH_RISCV64": 3,
}

BUILTIN_CONSTANTS = set(TARGET_CONSTANTS) | {"TARGET_OS", "TARGET_ARCH"}


def target_os(triple: str) -> str:
    """
    Classify a target triple's operating system component.
    """
    for part in triple.lower().split("-")[1:]:
        if "darwin" in part or "macos" in part:
            return "OS_DARWIN"

        if "linux" in part:
            return "OS_LINUX"

        if "windows" in part or part == "win32":
            return "OS_WINDOWS"

        if part == "none":
            return "OS_NONE"

    return "OS_UNKNOWN"


def target_arch(triple: str) -> str:
    """
    Classify a target triple's architecture component.
    """
    arch = triple.lower().split("-")[0]

    if arch in ("x86_64", "amd64"):
        return "ARCH_X86_64"

    if arch in ("aarch64", "arm64", "arm64e"):
        return "ARCH_AARCH64"

    if arch == "riscv64":
        return "ARCH_RISCV64"

    return "ARCH_UNKNOWN"


def register_builtin_constants(gen: CodeGenerator) -> None:
    """
    Define the target constants: every OS and architecture family, plus
    'TARGET_OS' and 'TARGET_ARCH' matching the compilation target.
    """
    values = dict(TARGET_CONSTANTS)
    values["TARGET_OS"] = values[target_os(gen.target)]
    values["TARGET_ARCH"] = values[target_arch(gen.target)]

    for name, value in values.items():
        gen.constants[name] = Const(name, None, IntLiteral(value))


def register_constants(gen: CodeGenerator, program: Program) -> None:
    """
    Register every '@const', then check each value is a constant expression.
    """
    register_builtin_constants(gen)

    for const in program.consts:
        with source_location(line=const.line, file=const.file):
            if const.name in BUILTIN_CONSTANTS:
                raise TypeError(f"constant {const.name!r} is defined by the compiler")

            if const.name in gen.constants:
                raise TypeError(f"constant {const.name!r} is declared more than once")

            const.type = expand_alias(gen, const.type)
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

    # a size is computed at compile time; its name resolves where the
    # constant is used
    if isinstance(expr, SizeOf):
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
