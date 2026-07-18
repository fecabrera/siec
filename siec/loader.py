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


def load_program(sources: list[Path], include_paths: list[Path]) -> Program:
    """
    Parse source files and their includes (recursively) into a single merged Program.
    """
    functions = []
    structs = []
    consts = []
    enums = []
    globals_ = []
    visited = set()

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

        # load includes depth-first so included declarations precede their includers
        for inc in program.includes:
            load(resolve_include(inc.path, file.parent, include_paths))

        # tag each declaration with its file so codegen errors can name it
        for struct in program.structs:
            struct.file = str(file)
        
        for fn in program.functions:
            fn.file = str(file)
        
        for const in program.consts:
            const.file = str(file)

        for enum in program.enums:
            enum.file = str(file)

        for glob in program.globals:
            glob.file = str(file)

        structs.extend(program.structs)
        functions.extend(program.functions)
        consts.extend(program.consts)
        enums.extend(program.enums)
        globals_.extend(program.globals)

    for source in sources:
        load(source)

    return Program([], functions, structs, consts, enums, globals_)
