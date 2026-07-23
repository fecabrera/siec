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
    TypeId,
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
        gen.constants[name] = [Const(name, None, IntLiteral(value), file=None)]


def register_constants(gen: CodeGenerator, program: Program) -> None:
    """
    Register every '@const', then check each value is a constant expression.

    Two files may declare the same name - each module keeps its own
    SEEK_SET - resolved by the user's view at each use; redeclaring
    within one file is the error it always was.
    """
    for const in program.consts:
        with source_location(line=const.line, file=const.file):
            if const.name in BUILTIN_CONSTANTS:
                raise TypeError(f"constant {const.name!r} is defined by the compiler")

            others = gen.constants.get(const.name, [])
            if (any(other.file == const.file for other in others)
                    or const.name in gen.macros):
                raise TypeError(f"constant {const.name!r} is declared more than once")

            gen.current_file = const.file

            # a macro registers its expansion for each use
            if const.is_macro:
                gen.macros[const.name] = const
                continue

            const.type = expand_alias(gen, const.type)
            gen.constants.setdefault(const.name, []).append(const)

    # validate after registration so constants may reference one another
    # regardless of declaration order
    for const in program.consts:
        with source_location(line=const.line, file=const.file):
            if const.is_macro:
                continue

            check_constant(gen, const.value, [const.name], const.file)

    # a macro calling itself, straight or roundabout, would expand forever
    from siec.codegen.macros import check_macro_cycles

    check_macro_cycles(gen)


def find_constant(gen: CodeGenerator, name: str, file: str | None = None):
    """
    The '@const' a name means in a file's view - the current file's when
    none is given. Nearer declarations shadow farther ones: the file's
    own or an include's, then a member import's module's, then the entry
    unit's; a builtin's everywhere. None when no declaration reaches the
    view; two equally near is ambiguous.
    """
    candidates = gen.constants.get(name)
    if not candidates:
        return None

    # one declaration program-wide needs no arbitration; visibility of
    # the name itself was the caller's 'sees' check
    if len(candidates) == 1:
        return candidates[0]

    file = file if file is not None else gen.current_file
    ranked = [(rank, c) for c in candidates
              if (rank := constant_rank(gen, c, name, file, frozenset())) is not None]
    if not ranked:
        return None

    best = min(rank for rank, _ in ranked)
    found = [c for rank, c in ranked if rank == best]

    if len(found) > 1:
        places = " and ".join(sorted(c.file or "<builtin>" for c in found))
        raise TypeError(f"constant {name!r} is ambiguous here: "
                        f"declared in {places}")

    return found[0]


def constant_rank(gen: CodeGenerator, const, binding: str,
                  file: str | None, active: frozenset) -> int | None:
    """
    How near a constant declaration sits to a file's unqualified use of
    'binding', mirroring how the loader builds each view: 0 the file's
    own or an include's, 1 a member import's, 2 the entry unit's; None
    out of reach.
    """
    # a builtin belongs to every view; a bare program is one namespace
    if const.file is None or not gen.include_closure or file is None:
        return 0

    # the file's own declarations and its includes', textual-style
    if const.file in gen.include_closure.get(file, {file}):
        return 0

    # a member import brings its module's declaration, under its binding
    member = gen.member_targets.get((file, binding))
    if member is not None:
        target, original = member
        if (const.name == original
                and const.file in gen.include_closure.get(target, {target})):
            return 1

    # the entry sources' views are in view everywhere, C-style
    for entry in gen.entry_files:
        if entry != file and entry not in active:
            if constant_rank(gen, const, binding, entry, active | {file}) is not None:
                return 2

    return None


def check_constant(gen: CodeGenerator, expr: Expr, chain: list[str],
                   file: str | None = None) -> None:
    """
    Reject anything but literals, operators, casts, and other constants in a
    constant's value, following references to catch cycles; references
    resolve in the declaring file's view.
    """
    if isinstance(expr, LITERALS):
        return

    # an enum member is a named integer constant; its existence is
    # checked where the constant is used
    if isinstance(expr, EnumMember):
        return

    # a size is computed at compile time, and a '@typeid' hash likewise;
    # their names resolve where the constant is used
    if isinstance(expr, (SizeOf, TypeId)):
        return

    if isinstance(expr, Var):
        other = find_constant(gen, expr.name, file)
        if other is None:
            raise TypeError(f"constant {chain[0]!r} references "
                            f"non-constant {expr.name!r}")

        if expr.name in chain:
            cycle = " -> ".join([*chain, expr.name])
            raise TypeError(f"constant cycle: {cycle}")

        check_constant(gen, other.value, [*chain, expr.name], other.file)
        return

    if isinstance(expr, UnaryOp):
        check_constant(gen, expr.operand, chain, file)
        return

    if isinstance(expr, BinaryOp):
        check_constant(gen, expr.left, chain, file)
        check_constant(gen, expr.right, chain, file)
        return

    if isinstance(expr, Cast):
        check_constant(gen, expr.operand, chain, file)
        return

    raise TypeError(f"constant {chain[0]!r} must be a constant expression")
