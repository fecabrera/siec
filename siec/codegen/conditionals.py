"""Resolution of '@if' conditional compilation blocks."""

from siec.ast import Program
from siec.codegen.aliases import register_aliases
from siec.codegen.constants import register_constants
from siec.codegen.enums import evaluate
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator


def resolve_conditionals(gen: CodeGenerator, program: Program) -> None:
    """
    Evaluate every '@if' block and splice the chosen branches' declarations
    into the program, so the registration passes see exactly the code the
    conditions selected.

    A branch's aliases and constants register on the spot: later conditions,
    including nested ones, may build on them.
    """
    for cond in program.conds:
        with source_location(line=cond.line, file=cond.file):
            # the condition's names resolve in its own file's view
            gen.current_file = cond.file
            branch = cond.then if evaluate(gen, cond.condition) else cond.orelse

        if branch is None:
            continue

        register_aliases(gen, branch)
        register_constants(gen, branch)
        resolve_conditionals(gen, branch)

        program.functions.extend(branch.functions)
        program.structs.extend(branch.structs)
        program.enums.extend(branch.enums)
        program.globals.extend(branch.globals)
        program.extends.extend(branch.extends)
