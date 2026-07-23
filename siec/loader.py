"""Loading of source files and resolution of includes."""

from pathlib import Path

from siec.ast import (BinaryOp, BoolLiteral, CharLiteral, CondBlock,
                      IntLiteral, Program, UnaryOp, Var)
from siec.lexer import lex
from siec.parser import parse


def holds_includes(cond: CondBlock) -> bool:
    """
    Whether an '@if' block reaches an '@include' in any of its branches,
    nested blocks included.
    """
    def search(branch: Program) -> bool:
        return bool(branch.includes) or any(holds_includes(c) for c in branch.conds)

    return search(cond.then) or (cond.orelse is not None and search(cond.orelse))


def evaluate_directive(expr, lookup, chain=()) -> int:
    """
    Evaluate the condition of an '@if' that guards an '@include'.

    The choice happens at load time, before the program assembles, so only
    literals, operators, the target constants, and '@const' values already
    in view can appear; enum members and 'sizeof' need the whole program
    and cannot.
    """
    # deferred import: the ops table lives with the codegen evaluator
    from siec.codegen.enums import BINARY_OPS

    if isinstance(expr, IntLiteral):
        return expr.value

    if isinstance(expr, BoolLiteral):
        return int(expr.value)

    if isinstance(expr, CharLiteral):
        return expr.value.encode()[0]

    if isinstance(expr, Var):
        if expr.name in chain:
            cycle = " -> ".join([*chain, expr.name])
            raise TypeError(f"constant cycle: {cycle}")

        value = lookup(expr.name)
        if value is None:
            raise TypeError(f"{expr.name!r} is not a constant in view here; "
                            "a condition guarding an '@include' evaluates "
                            "before the program assembles, so only the "
                            "target constants and '@const' values already "
                            "loaded can appear")

        return evaluate_directive(value, lookup, (*chain, expr.name))

    if isinstance(expr, UnaryOp) and expr.op in ("-", "~", "not"):
        value = evaluate_directive(expr.operand, lookup, chain)
        if expr.op == "not":
            return int(not value)

        return -value if expr.op == "-" else ~value

    if isinstance(expr, BinaryOp) and expr.op in BINARY_OPS:
        return BINARY_OPS[expr.op](evaluate_directive(expr.left, lookup, chain),
                                   evaluate_directive(expr.right, lookup, chain))

    raise TypeError("a condition guarding an '@include' evaluates before "
                    "the program assembles, so only literals, operators, "
                    "the target constants, and '@const' values already "
                    "loaded can appear")


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


def load_program(sources: list[Path], include_paths: list[Path],
                 target: str | None = None,
                 overlays: dict[str, str] | None = None) -> Program:
    """
    Parse source files and their includes (recursively) into a single merged Program.

    The target triple decides conditional includes; the host's when none
    is given, matching codegen.

    An overlay maps a file's resolved path to text that stands in for its
    on-disk contents: an editor's unsaved buffer, for a language server.
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
    member_targets = {}   # (file, binding) -> (module file, member name)
    exported = {}         # file -> its own exportable names
    declared_names = {}   # file -> every name it declares, statics included
    include_targets = {}  # file -> the files it includes
    member_names = {}     # file -> the names its member imports bind
    pending_members = []  # (file, import, target) checked once exports settle

    def declared(program: Program, with_statics: bool) -> set[str]:
        # the names a file declares: every top-level declaration, an '@if'
        # branch's counting whichever arm compilation later picks; statics
        # stay its own unless asked for
        names = ({fn.name for fn in program.functions
                  if with_statics or not fn.is_static}
                 | {glob.name for glob in program.globals
                    if with_statics or not glob.is_static}
                 | {const.name for const in program.consts}
                 | {struct.name for struct in program.structs}
                 | {enum.name for enum in program.enums}
                 | {alias.name for alias in program.aliases})

        for cond in program.conds:
            names |= declared(cond.then, with_statics)
            if cond.orelse is not None:
                names |= declared(cond.orelse, with_statics)

        return names

    def closure(base: dict) -> dict:
        # each file's names plus, transitively, its includes': an include
        # is textual, so the includer sees (and re-offers) what it pulled in
        memo = {}

        def visit(file: str, active: frozenset) -> set:
            if file in memo:
                return memo[file]

            if file in active:
                return base.get(file, set())

            names = set(base.get(file, set()))
            for target in include_targets.get(file, ()):
                names |= visit(target, active | {file})

            memo[file] = names
            return names

        for file in base:
            visit(file, frozenset())

        return memo

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

    builtin_values = {}

    def target_constant(name: str) -> int | None:
        # the target constants, computed on first use exactly as codegen
        # defines them: the OS and architecture families plus 'TARGET_OS'
        # and 'TARGET_ARCH' matching the compilation target
        if not builtin_values:
            from llvmlite import binding

            from siec.codegen.constants import (TARGET_CONSTANTS, target_arch,
                                                target_os)

            triple = target or binding.get_default_triple()
            builtin_values.update(TARGET_CONSTANTS)
            builtin_values["TARGET_OS"] = builtin_values[target_os(triple)]
            builtin_values["TARGET_ARCH"] = builtin_values[target_arch(triple)]

        return builtin_values.get(name)

    def load(file: Path) -> None:
        # visit each file once, keyed by absolute path; this also breaks include cycles
        file = file.resolve()
        if file in visited:
            return
        
        visited.add(file)

        # parse the file - its overlay text standing in when one is given -
        # tagging any lexer or parser error with its source
        text = overlays.get(str(file)) if overlays else None
        try:
            program = parse(lex(text if text is not None else file.read_text()))
        except (SyntaxError, TypeError, NameError) as error:
            if getattr(error, "sie_file", None) is None:
                error.sie_file = str(file)
            raise

        # record what the module offers before resolving its own imports,
        # so import cycles find it in place
        exported[str(file)] = declared(program, with_statics=False)
        declared_names[str(file)] = declared(program, with_statics=True)

        # load includes depth-first so included declarations precede their
        # includers; a failing one blames the file that wrote it
        def pull(inc):
            try:
                found = resolve_include(inc.path, file.parent, include_paths)
            except FileNotFoundError:
                error = FileNotFoundError(f"line {inc.line}: cannot resolve "
                                          f"include {inc.path!r}")
                error.sie_file = str(file)
                raise error from None

            load(found)
            include_targets.setdefault(str(file), []).append(str(found.resolve()))

        for inc in program.includes:
            pull(inc)

        # a conditional include loads only when its '@if' arm is chosen;
        # the condition evaluates now, against the target constants and
        # the '@const' values in view: this file's, its includes', and
        # earlier chosen arms'
        branch_consts = []

        def lookup(name):
            builtin = target_constant(name)
            if builtin is not None:
                return IntLiteral(builtin)

            for const in (*program.consts, *branch_consts, *consts):
                if const.name == name and not const.is_macro:
                    return const.value

            return None

        def follow(cond_blocks):
            for cond in cond_blocks:
                # an '@if' with no include in reach keeps its choice for
                # codegen, where the full constant language is in play
                if not holds_includes(cond):
                    continue

                try:
                    chosen = evaluate_directive(cond.condition, lookup)
                except TypeError as error:
                    error = TypeError(f"line {cond.line}: {error}")
                    error.sie_file = str(file)
                    raise error from None

                branch = cond.then if chosen else cond.orelse
                if branch is None:
                    continue

                branch_consts.extend(branch.consts)
                for inc in branch.includes:
                    pull(inc)

                follow(branch.conds)

        follow(program.conds)

        # load imports and record what each one binds in this file; a
        # failing one blames the file that wrote it
        for imp in program.imports:
            try:
                target = resolve_module(imp.path, file.parent, include_paths)
            except FileNotFoundError:
                error = FileNotFoundError(f"line {imp.line}: cannot resolve "
                                          f"import {imp.path!r}")
                error.sie_file = str(file)
                raise error from None

            load(target)
            target = str(target.resolve())

            if imp.members is not None:
                # membership is checked once every export set has settled
                pending_members.append((str(file), imp, target))
                for name, binding in imp.members:
                    member_bindings[(str(file), binding)] = name
                    member_targets[(str(file), binding)] = (target, name)
                    member_names.setdefault(str(file), set()).add(binding)
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

    # settle exports and visibility through the include chains
    module_exports = closure(exported)
    visible = closure(declared_names)

    # a member import must name something its module offers
    for file, imp, target in pending_members:
        for name, _ in imp.members:
            if name not in module_exports[target]:
                error = NameError(f"line {imp.line}: module {imp.path!r} "
                                  f"has no member {name!r}")
                error.sie_file = file
                raise error

    # member imports come into unqualified view; the command-line sources
    # form one compilation unit, their names in view everywhere, C-style
    entry_names = set()
    for source in sources:
        entry_names |= visible.get(str(source.resolve()), set())

    for file in visible:
        visible[file] |= member_names.get(file, set()) | entry_names

    # the unit's own files: the command-line sources and, textually, their
    # includes; a file reached only through 'import' sits outside it, so
    # separate compilation can leave its definitions to its own unit
    unit_files = set()
    stack = [str(source.resolve()) for source in sources]
    while stack:
        file = stack.pop()
        if file not in unit_files:
            unit_files.add(file)
            stack.extend(include_targets.get(file, ()))

    # each file with itself and its includes, transitively: the files
    # whose declarations it uses as its own
    include_closure = {}

    def close(file: str, active: frozenset) -> set:
        if file in include_closure:
            return include_closure[file]

        if file in active:
            return {file}

        files = {file}
        for target in include_targets.get(file, ()):
            files |= close(target, active | {file})

        include_closure[file] = files
        return files

    for file in declared_names:
        close(file, frozenset())

    merged = Program([], functions, structs, consts, enums, globals_, aliases, conds)
    merged.module_bindings = module_bindings
    merged.member_bindings = member_bindings
    merged.member_targets = member_targets
    merged.module_exports = module_exports
    merged.visible = visible
    merged.include_closure = include_closure
    merged.entry_files = [str(source.resolve()) for source in sources]
    merged.unit_files = unit_files
    return merged
