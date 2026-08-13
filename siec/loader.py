"""Phase 0: source discovery, selection, parsing, and unit assembly."""

import copy
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from siec.ast import (BinaryOp, BoolLiteral, CharLiteral, CondBlock,
                      IntLiteral, Program, UnaryOp, Var)
from siec.lexer import lex
from siec.parser import parse


@dataclass
class ParsedProgramCache:
    """
    Parsed source templates keyed by path and source identity.

    Loader tagging mutates declarations, so callers receive deep copies of
    cached templates. Unsaved overlays key by their exact text; files key by
    metadata that changes on normal writes and atomic replacements.
    """
    max_entries: int = 2048
    entries: dict[str, tuple[tuple, Program]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.max_entries = max(1, self.max_entries)

    @staticmethod
    def stamp(path: Path, overlay: str | None = None) -> tuple:
        if overlay is not None:
            return ("overlay", overlay)

        stat = path.stat()
        return ("file", stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)

    def read(self, path: Path, overlay: str | None = None) -> Program:
        path = path.resolve()
        key = str(path)
        stamp = self.stamp(path, overlay)
        cached = self.entries.get(key)
        if cached is not None and cached[0] == stamp:
            self.entries[key] = self.entries.pop(key)
            return copy.deepcopy(cached[1])

        text = overlay if overlay is not None else path.read_text()
        program = parse(lex(text))
        self.entries.pop(key, None)
        self.entries[key] = (stamp, program)
        while len(self.entries) > self.max_entries:
            self.entries.pop(next(iter(self.entries)))
        return copy.deepcopy(program)


def transitive_closure(graph: dict[str, list[str]],
                       origins: Iterable[str]) -> dict[str, set[str]]:
    """
    Every node reachable from each origin, including the origin itself.

    Each origin walks independently, so cycles cannot leave a memoized
    partial result whose contents depend on traversal order.
    """
    closure = {}
    for origin in origins:
        reached = set()
        pending = [origin]

        while pending:
            node = pending.pop()
            if node in reached:
                continue

            reached.add(node)
            pending.extend(graph.get(node, ()))

        closure[origin] = reached

    return closure


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
    in view can appear; enum members and '@sizeof' need the whole program
    and cannot.
    """
    # deferred import: the ops table lives with the codegen evaluator
    from siec.codegen.enums import BINARY_OPS, checked_result

    if isinstance(expr, IntLiteral):
        return checked_result(expr.value)

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

        return checked_result(-value if expr.op == "-" else ~value)

    if isinstance(expr, BinaryOp) and expr.op in BINARY_OPS:
        left = evaluate_directive(expr.left, lookup, chain)
        if expr.op == "and" and not left:
            return 0
        if expr.op == "or" and left:
            return 1

        return BINARY_OPS[expr.op](
            left, evaluate_directive(expr.right, lookup, chain))

    raise TypeError("a condition guarding an '@include' evaluates before "
                    "the program assembles, so only literals, operators, "
                    "the target constants, and '@const' values already "
                    "loaded can appear")


def resolve_include(path: str, includer_dir: Path, include_paths: list[Path],
                    dependencies: set[str] | None = None) -> Path:
    """
    Find the file for an include path, searching the includer's directory then the include paths.
    """
    # try each search root in order; the first hit wins
    for base in [includer_dir, *include_paths]:
        candidate = base / f"{path}.sie"
        if dependencies is not None:
            dependencies.add(str(candidate.resolve()))

        if candidate.is_file():
            return candidate

    raise FileNotFoundError(f"cannot resolve include {path!r}")


def resolve_module(path: str, importer_dir: Path, include_paths: list[Path],
                   dependencies: set[str] | None = None) -> Path:
    """
    Find the file for an import's dotted path: 'a.b' names 'a/b.sie',
    searched for in the importing file's directory, then the working
    directory, then the include paths.
    """
    relative = Path(*path.split(".")).with_suffix(".sie")

    for base in [importer_dir, Path.cwd(), *include_paths]:
        candidate = base / relative
        if dependencies is not None:
            dependencies.add(str(candidate.resolve()))

        if candidate.is_file():
            return candidate

    raise FileNotFoundError(f"cannot resolve import {path!r}")


def discover_program(sources: list[Path], include_paths: list[Path],
                     target: str | None = None,
                     overlays: dict[str, str] | None = None,
                     cache: ParsedProgramCache | None = None,
                     dependencies: set[str] | None = None) -> Program:
    """
    Discover and parse the selected source graph into one compilation unit.

    This is compilation phase 0. Imports and includes recursively discover the
    unit; conditions guarding includes select files with the loader-safe
    constant environment. Every selected file is parsed before this function
    returns, and no semantic type lookup occurs here.

    The target triple decides conditional includes; the host's when none
    is given, matching codegen.

    An overlay maps a file's resolved path to text that stands in for its
    on-disk contents: an editor's unsaved buffer, for a language server.

    When supplied, dependencies collects loaded files and every import/include
    candidate tested, including missing candidates that could later shadow the
    selected source.
    """
    functions = []
    structs = []
    consts = []
    enums = []
    globals_ = []
    aliases = []
    conds = []
    extends_ = []
    errors = []
    asserts = []
    visited = set()

    module_bindings = {}
    member_bindings = {}
    member_targets = {}   # (file, binding) -> (module file, member name)
    import_targets = {}   # (file, dotted path) -> resolved module root
    binding_sites = {}    # (file, import form, binding) -> (target, first line)
    exported = {}         # file -> its own exportable names
    declared_names = {}   # file -> every name it declares, statics included
    shared_names = {}     # file -> declarations shared across entry sources
    include_targets = {}  # file -> the files it includes
    member_names = {}     # file -> the names its member imports bind
    pending_members = []  # (file, import, target) checked once exports settle
    imported_roots = set()  # module files that start their own textual unit
    module_paths = {}        # module root -> stable dotted import spelling

    def claim_binding(file: str, binding: str, target: tuple,
                      line: int) -> bool:
        """
        Claim an import spelling in one file.

        Repeating the exact same import is harmless and binds nothing new.
        Pointing an existing spelling at another target of the same import
        form is an error. Module and member imports have distinct uses, so
        'import errno;' and 'import { errno } from errno;' may coexist.
        """
        form, *identity = target
        key = (file, form, binding)
        previous = binding_sites.get(key)
        if previous is None:
            binding_sites[key] = (identity, line)
            return True

        previous_target, previous_line = previous
        if previous_target == identity:
            return False

        error = NameError(f"line {line}: import binding {binding!r} is "
                          f"already declared at line {previous_line}")
        error.sie_file = file
        raise error

    def declared(program: Program, with_statics: bool,
                 with_private: bool = True) -> set[str]:
        # the names a file declares: every top-level declaration, an '@if'
        # branch's counting whichever arm compilation later picks. Statics
        # and private declarations stay out of module exports unless asked
        # for, while textual includes retain both.
        names = ({fn.name for fn in program.functions
                  if (with_statics or not fn.is_static)
                  and (with_private or not fn.is_private)}
                 | {glob.name for glob in program.globals
                    if with_statics or not glob.is_static}
                 | {const.name for const in program.consts
                    if with_private or not const.is_private}
                 | {struct.name for struct in program.structs
                    if with_private or not struct.is_private}
                 | {enum.name for enum in program.enums
                    if with_private or not enum.is_private}
                 | {alias.name for alias in program.aliases})

        for cond in program.conds:
            names |= declared(cond.then, with_statics, with_private)
            if cond.orelse is not None:
                names |= declared(cond.orelse, with_statics, with_private)

        return names

    def tag(program: Program, file: str) -> None:
        # tag each declaration with its file so codegen errors can name
        # it, into '@if' branches and all
        for decl in (*program.structs, *program.functions, *program.consts,
                     *program.enums, *program.globals, *program.aliases,
                     *program.extends, *program.errors, *program.asserts):
            decl.file = file

        for cond in program.conds:
            cond.file = file
            tag(cond.then, file)

            if cond.orelse is not None:
                tag(cond.orelse, file)

    builtin_values = {}

    def target_constant(name: str) -> int | None:
        # the target constants, computed on first use exactly as codegen
        # defines them: the OS, architecture, and environment families
        # plus 'TARGET_OS', 'TARGET_ARCH', and 'TARGET_ENV' matching the
        # compilation target
        if not builtin_values:
            from llvmlite import binding

            from siec.codegen.constants import (TARGET_CONSTANTS, target_arch,
                                                target_env, target_os)

            triple = target or binding.get_default_triple()
            builtin_values.update(TARGET_CONSTANTS)
            builtin_values["TARGET_OS"] = builtin_values[target_os(triple)]
            builtin_values["TARGET_ARCH"] = builtin_values[target_arch(triple)]
            builtin_values["TARGET_ENV"] = builtin_values[target_env(triple)]

        return builtin_values.get(name)

    def load(file: Path) -> None:
        # visit each file once, keyed by absolute path; this also breaks include cycles
        file = file.resolve()
        if file in visited:
            return
        
        visited.add(file)
        if dependencies is not None:
            dependencies.add(str(file))

        # parse the file - its overlay text standing in when one is given -
        # tagging any lexer or parser error with its source
        text = overlays.get(str(file)) if overlays else None
        try:
            program = (cache.read(file, text) if cache is not None
                       else parse(lex(text if text is not None
                                      else file.read_text())))
        except (SyntaxError, TypeError, NameError) as error:
            if getattr(error, "sie_file", None) is None:
                error.sie_file = str(file)
            raise

        # record what the module offers before resolving its own imports,
        # so import cycles find it in place
        exported[str(file)] = declared(
            program, with_statics=False, with_private=False)
        declared_names[str(file)] = declared(program, with_statics=True)
        shared_names[str(file)] = declared(
            program, with_statics=True, with_private=False)

        # load includes depth-first so included declarations precede their
        # includers; a failing one blames the file that wrote it
        def pull(inc):
            try:
                found = resolve_include(
                    inc.path,
                    file.parent,
                    include_paths,
                    dependencies,
                )
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
                target = resolve_module(
                    imp.path,
                    file.parent,
                    include_paths,
                    dependencies,
                )
            except FileNotFoundError:
                error = FileNotFoundError(f"line {imp.line}: cannot resolve "
                                          f"import {imp.path!r}")
                error.sie_file = str(file)
                raise error from None

            load(target)
            target = str(target.resolve())
            imported_roots.add(target)
            module_paths.setdefault(target, imp.path)
            import_targets[(str(file), imp.path)] = target

            if imp.members is not None:
                # membership is checked once every export set has settled
                pending_members.append((str(file), imp, target))
                for name, binding in imp.members:
                    if not claim_binding(str(file), binding,
                                         ("member", target, name), imp.line):
                        continue

                    member_bindings[(str(file), binding)] = name
                    member_targets[(str(file), binding)] = (target, name)
                    member_names.setdefault(str(file), set()).add(binding)
            else:
                binding = imp.alias or imp.path
                if claim_binding(str(file), binding, ("module", target), imp.line):
                    module_bindings[(str(file), binding)] = target

        tag(program, str(file))

        structs.extend(program.structs)
        functions.extend(program.functions)
        consts.extend(program.consts)
        enums.extend(program.enums)
        globals_.extend(program.globals)
        aliases.extend(program.aliases)
        conds.extend(program.conds)
        extends_.extend(program.extends)
        errors.extend(program.errors)
        asserts.extend(program.asserts)

    for source in sources:
        load(source)

    # Calculate the include graph once. Names inherit through the same
    # reachability relation used later for unit ownership and visibility.
    include_closure = transitive_closure(include_targets, declared_names)

    def inherited_names(base: dict[str, set[str]]) -> dict[str, set[str]]:
        inherited = {}
        for file, included in include_closure.items():
            names = set()
            for target in included:
                names.update(base.get(target, ()))
            inherited[file] = names
        return inherited

    module_exports = inherited_names(exported)
    visible = inherited_names(declared_names)
    shared = inherited_names(shared_names)

    # Private declarations flow throughout one textual module, in both
    # directions: an included file may use its includer's or a sibling
    # include's private name just as the root may use the included one's.
    # Imported module roots form their own groups and never join the caller.
    textual_roots = imported_roots | {
        str(source.resolve()) for source in sources
    }
    for root in textual_roots:
        files = include_closure.get(root, {root})
        private_names = set()
        for file in files:
            private_names.update(
                declared_names.get(file, set())
                - shared_names.get(file, set())
            )

        for file in files:
            visible.setdefault(file, set()).update(private_names)

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
        entry_names |= shared.get(str(source.resolve()), set())

    for file in visible:
        visible[file] |= member_names.get(file, set()) | entry_names

    # Imported modules are separate namespaces. Most declarations can retain
    # their public spelling internally, but two textual modules may export the
    # same concrete struct name (gtk.Application and adwaita.Application, for
    # example). Give only those collisions stable private identities, keeping
    # imports and diagnostics expressed through the original module members.
    entry_unit_files = set()
    for source in sources:
        resolved = str(source.resolve())
        entry_unit_files.update(include_closure.get(resolved, {resolved}))

    roots_by_file = {}
    for root in sorted(textual_roots):
        for file in include_closure.get(root, {root}):
            roots_by_file.setdefault(file, []).append(root)

    def declaration_roots(file: str) -> set[str]:
        """Every textual module surface containing ``file``."""
        roots = set(roots_by_file.get(file, ()))
        if file in entry_unit_files:
            roots.add("<entry>")
        return roots or {file}

    structs_by_name = {}
    for struct in structs:
        if struct.is_interface or struct.params is not None:
            continue
        structs_by_name.setdefault(struct.name, []).append(struct)

    local_type_symbols = {}
    module_type_symbols = {}
    for name, declarations in structs_by_name.items():
        # A declaration can belong to several module surfaces: an imported
        # aggregate may @include a file which is also reached by an import
        # cycle inside that aggregate. Same-named declarations sharing any
        # such surface describe one type, rather than one type per arbitrary
        # root. Form the transitive groups induced by shared roots.
        groups = []
        for declaration in declarations:
            roots = declaration_roots(declaration.file)
            touching = [group for group in groups if group[1] & roots]
            if not touching:
                groups.append(([declaration], set(roots)))
                continue

            members, combined = touching[0]
            members.append(declaration)
            combined.update(roots)
            for group in touching[1:]:
                members.extend(group[0])
                combined.update(group[1])
                groups.remove(group)

        if len(groups) < 2:
            continue

        identities = {}
        for members, roots in groups:
            stable_roots = [
                root if root == "<entry>"
                else module_paths.get(root, Path(root).stem)
                for root in sorted(roots)
            ]
            stable_root = "|".join(stable_roots)
            digest = hashlib.sha256(stable_root.encode()).hexdigest()[:12]
            identity = f"__module_{digest}_{name}"
            for declaration in members:
                identities[id(declaration)] = identity
            for root in roots:
                if root != "<entry>":
                    module_type_symbols[(root, name)] = identity
                files = (entry_unit_files if root == "<entry>"
                         else include_closure.get(root, {root}))
                for file in files:
                    local_type_symbols[(file, name)] = identity

        for declaration in declarations:
            declaration.name = identities[id(declaration)]

    # the unit's own files: the command-line sources and, textually, their
    # includes; a file reached only through 'import' sits outside it, so
    # separate compilation can leave its definitions to its own unit
    unit_files = entry_unit_files

    merged = Program([], functions, structs, consts, enums, globals_, aliases, conds)
    merged.extends = extends_
    merged.errors = errors
    merged.asserts = asserts
    merged.module_bindings = module_bindings
    merged.member_bindings = member_bindings
    merged.member_targets = member_targets
    merged.import_targets = import_targets
    merged.module_exports = module_exports
    merged.local_type_symbols = local_type_symbols
    merged.module_type_symbols = module_type_symbols
    merged.visible = visible
    merged.include_closure = include_closure
    merged.entry_files = [str(source.resolve()) for source in sources]
    merged.unit_files = unit_files
    return merged


def load_program(sources: list[Path], include_paths: list[Path],
                 target: str | None = None,
                 overlays: dict[str, str] | None = None,
                 cache: ParsedProgramCache | None = None,
                 dependencies: set[str] | None = None) -> Program:
    """Compatibility wrapper for :func:`discover_program`."""
    return discover_program(
        sources,
        include_paths,
        target,
        overlays,
        cache,
        dependencies,
    )
