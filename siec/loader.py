"""Loading of source files and resolution of includes."""

from pathlib import Path

from siec.ast import Program
from siec.lexer import lex
from siec.parser import parse


def resolve_include(path: str, includer_dir: Path, include_paths: list[Path]) -> Path:
    """
    Find the file for an include path, searching the includer's directory then the include paths.
    """
    # try each search root in order; the first hit wins
    for base in [includer_dir, *include_paths]:
        candidate = base / f"{path}.sie"

        if candidate.is_file():
            return candidate

    raise FileNotFoundError(f"cannot resolve include {path!r}")


def resolve_module(path: str, importer_dir: Path, include_paths: list[Path]) -> Path:
    """
    Find the file for an import's dotted path: 'a.b' names 'a/b.sie',
    searched for in the importing file's directory, then the working
    directory, then the include paths.
    """
    relative = Path(*path.split(".")).with_suffix(".sie")

    for base in [importer_dir, Path.cwd(), *include_paths]:
        candidate = base / relative

        if candidate.is_file():
            return candidate

    raise FileNotFoundError(f"cannot resolve import {path!r}")


def load_program(sources: list[Path], include_paths: list[Path]) -> Program:
    """
    Parse source files and their includes (recursively) into a single merged Program.
    """
    functions = []
    structs = []
    consts = []
    enums = []
    globals_ = []
    aliases = []
    conds = []
    visited = set()

    module_bindings = {}
    member_bindings = {}
    module_exports = {}

    def exports(program: Program) -> set[str]:
        # the names a module offers: every top-level declaration except
        # its statics, which stay its own; an '@if' branch's declarations
        # count, whichever arm compilation later picks
        names = ({fn.name for fn in program.functions if not fn.is_static}
                 | {glob.name for glob in program.globals if not glob.is_static}
                 | {const.name for const in program.consts}
                 | {struct.name for struct in program.structs}
                 | {enum.name for enum in program.enums}
                 | {alias.name for alias in program.aliases})

        for cond in program.conds:
            names |= exports(cond.then)
            if cond.orelse is not None:
                names |= exports(cond.orelse)

        return names

    def tag(program: Program, file: str) -> None:
        # tag each declaration with its file so codegen errors can name
        # it, into '@if' branches and all
        for decl in (*program.structs, *program.functions, *program.consts,
                     *program.enums, *program.globals, *program.aliases):
            decl.file = file

        for cond in program.conds:
            cond.file = file
            tag(cond.then, file)

            if cond.orelse is not None:
                tag(cond.orelse, file)

    def load(file: Path) -> None:
        # visit each file once, keyed by absolute path; this also breaks include cycles
        file = file.resolve()
        if file in visited:
            return
        
        visited.add(file)

        # parse the file, tagging any lexer or parser error with its source
        try:
            program = parse(lex(file.read_text()))
        except (SyntaxError, TypeError, NameError) as error:
            if getattr(error, "sie_file", None) is None:
                error.sie_file = str(file)
            raise

        # record what the module offers before resolving its own imports,
        # so import cycles find it in place
        module_exports[str(file)] = exports(program)

        # load includes depth-first so included declarations precede their includers
        for inc in program.includes:
            load(resolve_include(inc.path, file.parent, include_paths))

        # load imports and record what each one binds in this file
        for imp in program.imports:
            try:
                target = resolve_module(imp.path, file.parent, include_paths)
            except FileNotFoundError as error:
                error.sie_file = str(file)
                raise

            load(target)
            target = str(target.resolve())

            if imp.members is not None:
                for name, binding in imp.members:
                    if name not in module_exports[target]:
                        error = NameError(f"line {imp.line}: module "
                                          f"{imp.path!r} has no member {name!r}")
                        error.sie_file = str(file)
                        raise error

                    member_bindings[(str(file), binding)] = name
            else:
                module_bindings[(str(file), imp.alias or imp.path)] = target

        tag(program, str(file))

        structs.extend(program.structs)
        functions.extend(program.functions)
        consts.extend(program.consts)
        enums.extend(program.enums)
        globals_.extend(program.globals)
        aliases.extend(program.aliases)
        conds.extend(program.conds)

    for source in sources:
        load(source)

    merged = Program([], functions, structs, consts, enums, globals_, aliases, conds)
    merged.module_bindings = module_bindings
    merged.member_bindings = member_bindings
    merged.module_exports = module_exports
    return merged
