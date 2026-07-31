"""Resolution of '@if' conditional compilation blocks."""

from siec.ast import BinaryOp, EnumMember, Program, SizeOf, TypeId, UnaryOp, Var
from siec.codegen.aliases import register_aliases
from siec.codegen.constants import (constant_view, find_constant,
                                    register_constants)
from siec.codegen.enums import evaluate
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator


def check_asserts(gen: CodeGenerator, program: Program) -> None:
    """
    Check every '@static_assert' the compilation reached, once the whole
    program is registered: unlike an '@if', an assert gates no
    declaration, so its condition may weigh what those declarations
    turned out to be, a struct's '@sizeof' included.
    """
    for assertion in program.asserts:
        with source_location(line=assertion.line, file=assertion.file):
            # the condition's names resolve in its own file's view
            gen.current_file = assertion.file

            if not evaluate(gen, assertion.condition):
                raise TypeError("static assertion failed: "
                                f"{assertion.message}")


def needs_resolved_types(gen: CodeGenerator, expr, seen=frozenset()) -> bool:
    """
    Whether a condition reaches type meaning through an enum member,
    '@sizeof', '@typeid', or a constant containing one.

    Such a condition waits until the declaration inventory is available.
    An unknown variable waits too: an earlier deferred branch may select
    the constant it names, and the full pass will diagnose it if not.
    """
    if isinstance(expr, (EnumMember, SizeOf, TypeId)):
        return True

    if isinstance(expr, Var):
        const = find_constant(gen, expr.name,
                              getattr(expr, "module_file", None))
        if const is None:
            return True

        identity = id(const)
        if identity in seen:
            return False

        with constant_view(gen, const):
            return needs_resolved_types(gen, const.value,
                                        seen | {identity})

    if isinstance(expr, UnaryOp):
        return needs_resolved_types(gen, expr.operand, seen)

    if isinstance(expr, BinaryOp):
        return (needs_resolved_types(gen, expr.left, seen)
                or needs_resolved_types(gen, expr.right, seen))

    return False


def resolve_conditionals(gen: CodeGenerator, program: Program, *,
                         defer_types: bool = False,
                         register_branch=None) -> None:
    """
    Evaluate every '@if' block and splice the chosen branches' declarations
    into the program, so the registration passes see exactly the code the
    conditions selected.

    A branch's aliases and constants register on the spot: later conditions,
    including nested ones, may build on them. With 'defer_types', conditions
    needing resolved type information stay in 'program.conds' for a later
    pass. 'register_branch' lets that pass register a selected branch's type
    declarations before resolving nested conditions.
    """
    # an '@error' the compilation reaches stops it with its own message;
    # one in a branch is reached only when that branch is chosen, since
    # an unchosen one is never resolved
    for error in program.errors:
        with source_location(line=error.line, file=error.file):
            raise TypeError(error.message)

    pending = []
    for cond in program.conds:
        with source_location(line=cond.line, file=cond.file):
            # the condition's names resolve in its own file's view
            gen.current_file = cond.file
            if defer_types and needs_resolved_types(gen, cond.condition):
                pending.append(cond)
                continue

            chosen_then = bool(evaluate(gen, cond.condition))
            branch = cond.then if chosen_then else cond.orelse

        inactive = cond.orelse_span if chosen_then else cond.then_span
        if inactive is not None and cond.file:
            gen.inactive_regions.setdefault(cond.file, []).append(inactive)

        if branch is None:
            continue

        register_aliases(gen, branch)
        register_constants(gen, branch)
        if register_branch is not None:
            register_branch(branch)

        resolve_conditionals(
            gen,
            branch,
            defer_types=defer_types,
            register_branch=register_branch,
        )
        pending.extend(branch.conds)

        # a branch's asserts join the program's, checked once every
        # declaration is registered
        program.asserts.extend(branch.asserts)

        program.functions.extend(branch.functions)
        program.structs.extend(branch.structs)
        program.enums.extend(branch.enums)
        program.globals.extend(branch.globals)
        program.aliases.extend(branch.aliases)
        program.consts.extend(branch.consts)
        program.extends.extend(branch.extends)

    program.conds = pending
