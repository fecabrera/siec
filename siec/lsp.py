"""The Sie language server, speaking LSP over stdio.

The server reuses the compiler's front end wholesale: diagnostics come
from running the loader and codegen over the editor's buffers, and the
document outline from the parser's AST. Analysis compiles each file as
its own unit, imports declaring only, like '-c': the edited file's
errors surface without emitting every imported module behind it.

'sie-lsp' starts it; the 'editors/' directory holds the VSCode and
Helix setups that connect it to '.sie' files.
"""

import asyncio
import re
import sys
import traceback
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import wraps
from inspect import isawaitable, iscoroutinefunction
from pathlib import Path
from typing import TYPE_CHECKING

from siec.ast import Body, For, Function, Let, LocalFunction, Program
from siec.cli import error_parts
from siec.codegen import CodeGenerator, codegen
from siec.codegen.generator import Variable
from siec.codegen.types import is_reference, strip_const, strip_reference
from siec.diagnostics import DiagnosticError
from siec.lexer import Token, lex
from siec.loader import ParsedProgramCache, discover_program
from siec.parser import parse

if TYPE_CHECKING:
    from siec.sie import PackageManifest


@dataclass
class Report:
    """
    A compile error located for the editor: the resolved file it belongs
    to, its 1-based line (None when unknown), and the bare message.
    """
    file: str
    line: int | None
    message: str


@dataclass
class Analysis:
    """
    One unit's compiled state: the merged program and the generator that
    emitted it, however far emission got, plus the first error. The
    generator's tables are the semantic index hover and go-to-definition
    read; both are None when the sources never parsed.
    """
    path: str
    report: Report | None
    program: Program | None = None
    gen: CodeGenerator | None = None
    overlays: dict[str, str] | None = None
    files: frozenset[str] = frozenset()
    diagnostics: tuple = ()


@dataclass
class _CachedAnalysis:
    """One reusable full-unit analysis and the inputs that produced it."""
    paths: tuple[str, ...]
    stamps: dict[str, tuple]
    analysis: Analysis


class UnitAnalysisCache:
    """
    LSP front-end and full-unit cache.

    Parsed dependencies are shared across recompilations. A complete analysis
    is reused only while its include path and every resolution-input stamp match.
    """

    def __init__(self, max_units: int = 256):
        self.max_units = max(1, max_units)
        self.parsed = ParsedProgramCache()
        self.units: dict[str, _CachedAnalysis] = {}

    @staticmethod
    def path_key(paths: list[Path]) -> tuple[str, ...]:
        return tuple(str(path.resolve()) for path in paths)

    def stamps(self, files, overlays: dict[str, str] | None):
        found = {}
        for file in files:
            path = Path(file)
            overlay = overlays.get(file) if overlays else None
            try:
                found[file] = self.parsed.stamp(path, overlay)
            except FileNotFoundError:
                found[file] = ("missing",)
            except OSError:
                return None

        return found

    def get(self, path: Path, paths: list[Path],
            overlays: dict[str, str] | None) -> Analysis | None:
        cached = self.units.get(str(path))
        if cached is None or cached.paths != self.path_key(paths):
            return None

        current = self.stamps(cached.stamps, overlays)
        if current != cached.stamps:
            return None

        self.units[str(path)] = self.units.pop(str(path))
        return cached.analysis

    def put(self, path: Path, paths: list[Path],
            overlays: dict[str, str] | None, analysis: Analysis) -> None:
        stamps = self.stamps(analysis.files, overlays)
        if stamps is not None:
            self.units.pop(str(path), None)
            self.units[str(path)] = _CachedAnalysis(
                self.path_key(paths), stamps, analysis)
            while len(self.units) > self.max_units:
                self.units.pop(next(iter(self.units)))
        else:
            self.units.pop(str(path), None)

    def discard(self, path: Path) -> None:
        self.units.pop(str(path.resolve()), None)


@dataclass
class _CachedPaths:
    """Resolved include paths and the filesystem entries that selected them."""
    watched: dict[Path, tuple | None]
    paths: list[Path]


class SearchPathCache:
    """Cache manifest/package resolution until any relevant entry changes."""

    def __init__(self, max_entries: int = 256):
        self.max_entries = max(1, max_entries)
        self.entries: dict[tuple, _CachedPaths] = {}

    @staticmethod
    def stamp(path: Path) -> tuple | None:
        try:
            stat = path.stat()
        except OSError:
            return None

        return (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)

    @staticmethod
    def key(root: Path | None, extra: list[str],
            file_dir: Path | None) -> tuple:
        from siec.sie import install_root

        return (
            str(root.resolve()) if root is not None else None,
            tuple(extra),
            str(file_dir.resolve()) if file_dir is not None else None,
            str(install_root().resolve()),
        )

    def get(self, root: Path | None, extra: list[str],
            file_dir: Path | None) -> list[Path]:
        key = self.key(root, extra, file_dir)
        cached = self.entries.get(key)
        if cached is not None:
            current = {path: self.stamp(path) for path in cached.watched}
            if current == cached.watched:
                self.entries[key] = self.entries.pop(key)
                return list(cached.paths)

        watched: set[Path] = set()
        paths = _search_paths(root, extra, file_dir, watched)
        self.entries.pop(key, None)
        self.entries[key] = _CachedPaths(
            {path: self.stamp(path) for path in watched},
            list(paths),
        )
        while len(self.entries) > self.max_entries:
            self.entries.pop(next(iter(self.entries)))
        return paths


@dataclass
class Finding:
    """
    What the name under the cursor resolved to: its declaration in Sie
    syntax for hover, and the declaration sites for go-to-definition as
    (file, 1-based line) pairs.
    """
    text: str
    targets: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class Symbol:
    """
    One outline entry: a top-level declaration's name, kind, and 1-based
    line. Kinds: 'function', 'method', 'struct', 'interface', 'enum',
    'constant', 'variable', 'alias'.
    """
    name: str
    kind: str
    line: int


@dataclass(frozen=True)
class Completion:
    """One editor completion candidate, independent of the LSP transport."""
    label: str
    kind: str
    detail: str | None = None


KEYWORD_COMPLETIONS = (
    "and", "as", "bool", "break", "case", "char", "const", "continue",
    "defer", "else", "emit", "enum", "false", "f32", "f64", "fn",
    "for", "foreach", "i8", "i16", "i32", "i64", "i128", "if", "import",
    "interface", "let", "not", "opaque", "or", "raw", "return",
    "struct", "true", "u8", "u16", "u32", "u64", "u128", "union", "when",
    "while",
)

TYPE_KEYWORD_COMPLETIONS = frozenset((
    "bool", "char", "f32", "f64", "i8", "i16", "i32", "i64", "i128",
    "opaque", "raw", "u8", "u16", "u32", "u64", "u128",
))


def inactive_semantic_tokens(analysis: Analysis, text: str) -> list[int]:
    """
    Encode unchosen '@if' branch contents as LSP semantic tokens.

    They use the standard 'comment' token type (legend index zero), which
    editors and themes already render as subdued text. LSP positions count
    UTF-16 code units, while the parser's columns count Python characters.
    """
    if analysis.gen is None:
        return []

    # Never paint stale spans over a buffer newer than its analysis.
    analyzed = (analysis.overlays or {}).get(analysis.path)
    if analyzed is not None and analyzed != text:
        return []

    lines = text.splitlines()
    tokens = []
    for start_line, start_col, end_line, end_col in (
            analysis.gen.inactive_regions.get(analysis.path, ())):
        for line_number in range(start_line, end_line + 1):
            if not 1 <= line_number <= len(lines):
                continue

            line = lines[line_number - 1]
            begin = start_col if line_number == start_line else 0
            end = end_col if line_number == end_line else len(line)
            begin = min(begin, len(line))
            end = min(max(end, begin), len(line))
            if begin == end:
                continue

            utf16_begin = len(line[:begin].encode("utf-16-le")) // 2
            utf16_length = len(line[begin:end].encode("utf-16-le")) // 2
            tokens.append((line_number - 1, utf16_begin, utf16_length))

    # Semantic tokens delta-encode their line and start column.
    encoded = []
    previous_line = previous_start = 0
    for line, start, length in sorted(tokens):
        delta_line = line - previous_line
        delta_start = start - previous_start if delta_line == 0 else start
        encoded.extend((delta_line, delta_start, length, 0, 0))
        previous_line, previous_start = line, start

    return encoded


class _ValidationDebouncer:
    """Own and retire delayed validation tasks, one per document URI."""

    def __init__(self, validate: Callable, delay: float = 0.2,
                 on_error: Callable | None = None):
        self.validate = validate
        self.delay = delay
        self.on_error = on_error
        self.pending: dict[str, asyncio.Task] = {}

    async def _invoke(self, uri: str) -> None:
        """Run async callbacks directly and legacy sync callbacks off-loop."""
        if iscoroutinefunction(self.validate):
            await self.validate(uri)
        else:
            result = await asyncio.to_thread(self.validate, uri)
            if isawaitable(result):
                await result

    async def _failed(self, uri: str, error: Exception) -> None:
        """Consume an unexpected failure and hand it to the server boundary."""
        if self.on_error is None:
            return
        try:
            result = self.on_error(uri, error)
            if isawaitable(result):
                await result
        except Exception:
            # Error reporting must never create another unobserved task
            # exception. Server callbacks log defensively themselves.
            pass

    def _replace(self, uri: str, delay: float) -> asyncio.Task:
        """Install one validation task, superseding this URI's older work."""
        self.cancel(uri)

        async def settled() -> None:
            try:
                if delay:
                    await asyncio.sleep(delay)
                await self._invoke(uri)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self._failed(uri, error)
            finally:
                # A canceled predecessor may finish after its replacement was
                # installed; only the task that still owns the URI may remove it.
                if self.pending.get(uri) is asyncio.current_task():
                    self.pending.pop(uri, None)

        task = asyncio.get_running_loop().create_task(settled())
        self.pending[uri] = task
        return task

    def schedule(self, uri: str) -> None:
        """Replace any validation waiting or running for the same document."""
        self._replace(uri, self.delay)

    async def run_now(self, uri: str) -> None:
        """Supersede older work and await an immediate off-loop validation."""
        task = self._replace(uri, 0)
        try:
            await task
        except asyncio.CancelledError:
            # A newer edit or a close owns the URI now.
            if self.pending.get(uri) is task:
                raise

    def cancel(self, uri: str) -> None:
        """Cancel and forget a document's delayed or running validation."""
        if (task := self.pending.pop(uri, None)) is not None:
            task.cancel()

    def cancel_all(self) -> None:
        """Cancel every validation while preserving per-task retirement."""
        for uri in list(self.pending):
            self.cancel(uri)


def package_paths(root: "PackageManifest",
                  watched: set[Path] | None = None) -> list[Path]:
    """
    The include path a package's own manifest implies: the directories
    its sources sit in, then those of every dependency resolved from
    what is installed, exactly as 'sie build' assembles them.

    A dependency that resolves to nothing simply contributes nothing:
    the editor still analyzes what it can, and the unresolved import
    reports itself.
    """
    from siec.sie import MANIFEST, install_root, installed, resolve

    # Neither an app nor a library: a project's own configuration, which
    # speaks through '[package] include' instead.
    if root.kind is None:
        return []

    if watched is not None:
        # Package selection changes when versions are added or removed, and
        # dependency graphs change when any installed manifest is rewritten.
        watched.add(install_root())
        watched.update(path / MANIFEST for _, _, path in installed())

    try:
        tree = resolve(root)
    except LookupError:
        tree = []

    paths: list[Path] = []
    for member in (root, *tree):
        if watched is not None:
            watched.add(member.path / MANIFEST)
        for directory in member.source_dirs():
            if directory not in paths:
                paths.append(directory)

    return paths


def config_paths(file_dir: Path | None, root: Path | None,
                 watched: set[Path] | None = None) -> list[Path]:
    """
    Include paths from the project's 'package.toml' files: the nearest
    one walking up from the edited file, then the workspace root's.

    Each contributes its '[package] include' entries relative to itself,
    and, where it declares a package of its own, that package's sources
    and its installed dependencies'.

    Configured directories come first, whichever manifest named them: an
    'include' entry is a deliberate override, so a checkout pointing at
    its own packages wins over the copies a dependency resolves to.
    """
    from siec.sie import load_package

    configured: list[Path] = []
    packaged: list[Path] = []
    read: set[Path] = set()

    def take(base: Path) -> None:
        toml = base / "package.toml"
        if watched is not None:
            watched.add(toml)
        if not toml.is_file() or toml in read:
            return

        read.add(toml)
        try:
            model = load_package(toml)
        except ValueError:
            return

        for entry in model.includes:
            configured.append((model.path / entry).resolve())

        packaged.extend(package_paths(model, watched))

    if file_dir is not None:
        for base in (file_dir, *file_dir.parents):
            manifest = base / "package.toml"
            if watched is not None:
                watched.add(manifest)
            if manifest.is_file():
                take(base)
                break

            if root is not None and base == root:
                break

    if root is not None:
        take(root)

    return [*configured, *packaged]


def _search_paths(root: Path | None, extra: list[str],
                  file_dir: Path | None = None,
                  watched: set[Path] | None = None) -> list[Path]:
    """Uncached include-path resolution, recording its filesystem inputs."""
    paths = [Path(p) for p in extra]
    paths.extend(config_paths(file_dir, root, watched))

    if root is not None:
        paths.extend((root, root / "lib"))

    return paths


def search_paths(root: Path | None, extra: list[str],
                 file_dir: Path | None = None,
                 cache: SearchPathCache | None = None) -> list[Path]:
    """
    The include path for analysis: the configured directories first, the
    project's 'package.toml' entries next, and the workspace root with
    its 'lib/', mirroring the compiler's own search.
    """
    if cache is not None:
        return cache.get(root, extra, file_dir)

    return _search_paths(root, extra, file_dir)


def compile_unit(path: Path, include_paths: list[Path],
                 overlays: dict[str, str] | None = None,
                 cache: UnitAnalysisCache | None = None) -> Analysis:
    """
    Compile one file as its own unit, keeping whatever the front end
    built: the merged program, the generator, and the first error.

    Overlays stand in for on-disk contents, so unsaved edits analyze
    live; nothing is emitted to native code.
    """
    path = path.resolve()
    paths = [*include_paths, path.parent / "lib"]
    if cache is not None:
        cached = cache.get(path, paths, overlays)
        if cached is not None:
            return cached

    gen = CodeGenerator(str(path))
    program = None
    report = None
    dependencies: set[str] = set()
    try:
        program = discover_program(
            [path],
            paths,
            overlays=overlays,
            cache=cache.parsed if cache is not None else None,
            dependencies=dependencies,
        )
        codegen(program, str(path), define_imports=False, gen=gen)
    except (DiagnosticError, SyntaxError, TypeError, NameError,
            FileNotFoundError) as error:
        file, line, message = error_parts(error)
        report = Report(file or str(path), line, message)

    # Codegen owns a rewritten clone of the parsed AST. Keep that semantic
    # view for hover/navigation while leaving the loader's tree untouched.
    analyzed = gen.program if gen.program is not None else program
    analysis = Analysis(
        str(path),
        report,
        analyzed,
        gen if analyzed is not None else None,
        dict(overlays or {}),
        frozenset(dependencies),
        tuple(gen.diagnostics),
    )
    if cache is not None:
        cache.put(path, paths, overlays, analysis)
    return analysis


def dependent_uris(loaded_files: dict[str, frozenset[str]],
                   changed_path: Path) -> list[str]:
    """Open units whose source-resolution inputs contain the changed file."""
    changed = str(changed_path.resolve())
    return [uri for uri, files in loaded_files.items() if changed in files]


def analyze(path: Path, include_paths: list[Path],
            overlays: dict[str, str] | None = None) -> Report | None:
    """
    Compile one file as its own unit, returning the first compile error
    or None when it is clean.
    """
    return compile_unit(path, include_paths, overlays).report


def outline(text: str) -> list[Symbol] | None:
    """
    The text's top-level declarations in source order, or None when it
    does not parse - the caller keeps the last good outline then.
    """
    try:
        program = parse(lex(text))
    except (DiagnosticError, SyntaxError, TypeError, NameError):
        return None

    symbols: list[Symbol] = []

    def collect(program) -> None:
        for fn in program.functions:
            kind = "method" if fn.receiver is not None else "function"
            symbols.append(Symbol(fn.name, kind, fn.line))

        for struct in program.structs:
            kind = "interface" if struct.is_interface else "struct"
            symbols.append(Symbol(struct.name, kind, struct.line))

        for enum in program.enums:
            symbols.append(Symbol(enum.name, "enum", enum.line))

        for const in program.consts:
            kind = "macro" if const.is_macro else "constant"
            symbols.append(Symbol(const.name, kind, const.line))

        for glob in program.globals:
            symbols.append(Symbol(glob.name, "variable", glob.line))

        for alias in program.aliases:
            symbols.append(Symbol(alias.name, "alias", alias.line))

        # both arms show: the outline is lexical, not compiled
        for cond in program.conds:
            collect(cond.then)
            if cond.orelse is not None:
                collect(cond.orelse)

    collect(program)
    symbols.sort(key=lambda s: s.line)
    return symbols


def token_chain(tokens: list[Token], line: int, col: int,
                text: str | None = None):
    """
    The name chain ending at the cursor: the identifier at the 0-based
    position, walked back through its '.' and '::' links. Returns the
    parts, the separators between them, the cursor's token, and the
    syntax following the chain; None off any identifier.

    A chain hanging off a wider expression ('get(i).x') carries that
    receiver's source spelling as its first part, so the compiler's
    expression inference can type it.
    """
    at = next((i for i, t in enumerate(tokens)
               if t.kind == "ident" and t.line == line + 1
               and t.col <= col < t.col + len(t.value)), None)
    if at is None:
        return None

    start = at
    while (start >= 2 and tokens[start - 1].syntax in (".", "::")
           and tokens[start - 2].kind == "ident"):
        start -= 2

    # A link into a non-name receiver keeps the expression to its left:
    # 'self.as_array().format' carries 'self.as_array()' as the receiver.
    if start >= 1 and tokens[start - 1].syntax in (".", "::"):
        if tokens[start - 1].syntax == "." and text is not None:
            receiver = expression_receiver(
                tokens, start - 2, tokens[start - 1], text)
            if receiver is not None:
                following = (
                    tokens[at + 1].syntax if at + 1 < len(tokens) else None)
                return ([receiver, tokens[at].value], ["."],
                        tokens[at], following)
        return None

    parts = [tokens[i].value for i in range(start, at + 1, 2)]
    seps = [tokens[i].syntax for i in range(start + 1, at, 2)]
    following = tokens[at + 1].syntax if at + 1 < len(tokens) else None
    return parts, seps, tokens[at], following


def expression_receiver(tokens: list[Token], end: int, dot: Token,
                        text: str) -> str | None:
    """
    The source spelling of an expression ending immediately before a
    member-access dot. Balanced call, index, and grouped delimiters stay
    inside it; a surrounding statement, operator, or argument starts the
    boundary.
    """
    if end < 0:
        return None

    opens = {"(": ")", "[": "]", "{": "}"}
    closes = {value: key for key, value in opens.items()}
    boundaries = {
        ";", ",", "=", "?", ":",
        "+", "-", "*", "/", "%", "**",
        "==", "!=", "<", ">", "<=", ">=",
        "&", "|", "^", "&&", "||",
        "and", "or",
        "return", "emit", "let", "if", "while", "for", "foreach",
        "case", "when", "defer", "drop",
    }

    depth: list[str] = []
    start = end
    for index in range(end, -1, -1):
        syntax = tokens[index].syntax
        if syntax in closes:
            depth.append(closes[syntax])
        elif syntax in opens:
            if not depth:
                start = index + 1
                break
            if depth[-1] != syntax:
                return None
            depth.pop()
        elif not depth and syntax in boundaries:
            start = index + 1
            break

        start = index

    if depth or start > end:
        return None

    line_offsets = [0]
    for index, char in enumerate(text):
        if char == "\n":
            line_offsets.append(index + 1)

    first = tokens[start]
    if first.line > len(line_offsets) or dot.line > len(line_offsets):
        return None

    begin = line_offsets[first.line - 1] + first.col
    finish = line_offsets[dot.line - 1] + dot.col
    receiver = text[begin:finish].strip()
    return receiver or None


def span_contains(body: list, line: int, col: int) -> bool:
    """
    Whether a 1-based source position is inside a parsed statement body.
    Body spans are half-open, so an adjacent `else` or following declaration
    does not belong to the body ending immediately before it.
    """
    span = getattr(body, "span", None)
    if span is None:
        return False

    start_line, start_col, end_line, end_col = span
    return (start_line, start_col) <= (line, col) < (end_line, end_col)


def enclosing_function(program: Program, file: str, line: int,
                       col: int = 0) -> Function | None:
    """
    The defined function whose body contains the 1-based source position.
    """
    return next((
        fn for fn in program.functions
        if fn.file == file and fn.body is not None
        and span_contains(fn.body, line, col)
    ), None)


def local_scope(gen: CodeGenerator, fn: Function, line: int, col: int = 0):
    """
    The names in scope at a 1-based line of a function's body, each with
    its declared or inferred type and its declaring line.

    Only declarations in the lexical body chain containing the cursor
    count. A nested body's locals therefore disappear at its closing brace,
    while declarations in its ancestors remain visible.
    """
    from dataclasses import fields as dataclass_fields, is_dataclass

    from siec.codegen.inference import infer_type

    scope: dict[str, Variable] = {}
    lines: dict[str, int] = {}

    from siec.codegen.checking import bind_tuple_pattern

    for param in fn.params:
        scope[param.name] = Variable(None, param.type)
        lines[param.name] = fn.line
        # Destructured tuple params bind their pattern names for hover;
        # the synthetic '#N' slot is not a usable source name.
        if param.pattern is not None:
            before = set(scope)
            bind_tuple_pattern(gen, param.pattern, param.type, scope)
            for name in scope.keys() - before:
                lines[name] = fn.line

    def declare(node: Let) -> None:
        type_ = node.type
        if type_ is None and node.value is not None:
            try:
                type_ = infer_type(gen, node.value, dict(scope))
            except (TypeError, NameError, RuntimeError):
                # After compilation, inferring a still-free generic (or a
                # cast that would stamp one) must not reopen LLVM lowering.
                type_ = None

        if type_ is not None:
            scope[node.name] = Variable(None, type_)

        lines[node.name] = node.line

    def declare_local_function(node: LocalFunction) -> None:
        from siec.codegen.closures import closure_type

        scope[node.name] = Variable(None, closure_type(node.value))
        lines[node.name] = node.line

    def active_body(node) -> Body | None:
        """
        The immediate nested body containing the cursor, wherever a
        statement carries it: control flow, a try arm, or a block expression.
        """
        if isinstance(node, Body):
            return node if span_contains(node, line, col) else None

        if isinstance(node, (list, tuple)):
            for item in node:
                if (found := active_body(item)) is not None:
                    return found
            return None

        if not is_dataclass(node):
            return None

        for field_ in dataclass_fields(node):
            if (found := active_body(getattr(node, field_.name))) is not None:
                return found

        return None

    def walk(body: Body) -> None:
        for statement in body:
            nested = active_body(statement)
            if nested is not None:
                # A for initializer belongs to its condition, step, and body,
                # but not to the surrounding body after the loop.
                if isinstance(statement, For) and isinstance(statement.init, Let):
                    declare(statement.init)

                walk(nested)
                return

            # A direct declaration enters this body after its initializer.
            # The line check retains the previous same-line approximation;
            # the body's span now supplies the missing lexical boundary.
            if (isinstance(statement, Let) and statement.line
                    and statement.line <= line):
                declare(statement)
            elif (isinstance(statement, LocalFunction) and statement.line
                  and statement.line <= line):
                declare_local_function(statement)

    walk(fn.body)
    return scope, lines


def pattern_text(pattern: list) -> str:
    """Render a tuple parameter pattern as Sie source, e.g. '(m, e, neg)'."""
    parts = [
        pattern_text(item) if isinstance(item, list) else item
        for item in pattern
    ]
    return f"({', '.join(parts)})"


def signature(fn: Function) -> str:
    """
    A function's declaration in Sie syntax, its generic parameters kept.

    An interface-typed parameter became a synthetic constrained type
    parameter at registration; it renders back as the interface it was
    declared with. A destructured tuple parameter keeps its pattern
    rather than the synthetic '#N' spill name.
    """
    from siec.codegen.generics import substitute

    def bound_text(value) -> str:
        return " & ".join(value if isinstance(value, tuple) else (value,))

    mapping = {}
    type_params = list(fn.type_params or ())
    for param, constraint in (fn.constraints or {}).items():
        if param.startswith("__"):
            mapping[param] = constraint
            if param in type_params:
                type_params.remove(param)

    name = fn.name
    if (fn.receiver_params and fn.receiver
            and fn.receiver not in fn.receiver_params
            and not fn.receiver.endswith("[]")):
        receiver_params = ", ".join(
            p + (f": {bound_text(fn.receiver_constraints[p])}"
                 if p in (fn.receiver_constraints or {}) else "")
            for p in fn.receiver_params
        )
        name = (f"{fn.receiver}<{receiver_params}>"
                f"::{fn.name.partition('::')[2]}")

    if type_params:
        shown = ", ".join(
            p + (f": {bound_text(fn.constraints[p])}"
                 if p in (fn.constraints or {}) else "")
            for p in type_params
        )
        name += f"<{shown}>"

    def param_text(p) -> str:
        if p.name == "self" and is_reference(strip_const(p.type)):
            return p.type

        type_name = substitute(p.type, mapping)
        if p.pattern is not None:
            return f"{pattern_text(p.pattern)}: {type_name}"
        return f"{p.name}: {type_name}"

    params = ", ".join(param_text(p) for p in fn.params)
    ret = f" -> {fn.return_type}" if fn.return_type else ""
    return f"fn {name}({params}){ret}"


def struct_text(node) -> str:
    """
    A struct's declaration in Sie syntax, its fields listed.
    """
    kind = "interface" if node.is_interface else \
        "union" if getattr(node, "is_union", False) else "struct"
    name = node.name
    if node.params:
        def bound_text(value) -> str:
            bounds = value if isinstance(value, tuple) else (value,)
            return " & ".join(bounds)

        params = ", ".join(
            p + (f": {bound_text(node.constraints[p])}"
                 if p in (node.constraints or {}) else "")
            for p in node.params
        )
        name += f"<{params}>"

    if node.is_interface or not node.fields:
        return f"{kind} {name};"

    fields = "\n".join(f"    {f.name}: {f.type};" for f in node.fields)
    return f"{kind} {name} {{\n{fields}\n}}"


def enum_text(node) -> str:
    """
    An enum's declaration in Sie syntax, its members listed by name.
    """
    members = ", ".join(v.name for v in node.members)
    return f"enum {node.name} {{ {members} }}"


def source_line(analysis: Analysis, file: str, line: int) -> str | None:
    """
    One 1-based line of a file's text, overlay first, stripped.
    """
    text = (analysis.overlays or {}).get(file)
    if text is None:
        try:
            text = Path(file).read_text()
        except OSError:
            return None

    lines = text.splitlines()
    if 0 < line <= len(lines):
        return lines[line - 1].strip()

    return None


def navigable_file(file: str) -> bool:
    """
    Whether a declaration's source names a real editor document.

    Angle-bracket names identify compiler-owned virtual sources such as the
    builtin prelude: useful in diagnostics, but not valid definition targets.
    """
    return bool(file) and not (file.startswith("<") and file.endswith(">"))


def declaration_sites(program: Program):
    """
    Every top-level declaration by name: (kind, node) pairs, the prelude's
    included (its virtual file remains unnavigable).
    """
    index: dict[str, list] = {}

    def collect(program: Program) -> None:
        for kind, decls in (("function", program.functions),
                            ("struct", program.structs),
                            ("enum", program.enums),
                            ("constant", program.consts),
                            ("variable", program.globals),
                            ("alias", program.aliases)):
            for decl in decls:
                index.setdefault(decl.name, []).append((kind, decl))

        for cond in program.conds:
            collect(cond.then)
            if cond.orelse is not None:
                collect(cond.orelse)

    collect(program)
    return index


def complete(analysis: Analysis, text: str, line: int,
             col: int) -> list[Completion]:
    """
    Complete names at a 0-based source position.

    A trailing dotted receiver first checks the loader's resolved module
    bindings. Thus ``import util; util.`` offers exactly ``util``'s public
    exports, including declarations supplied by that module's includes.
    A trailing ``p->`` offers that pointer's pointee fields and methods,
    matching the parser's ``(*p).field`` desugaring.
    A trailing ``Type::`` offers that type's enum members or methods.
    Inside a named aggregate ``{ field = ... }``, offers the expected
    struct's remaining fields.
    Everywhere else, locals, names visible to this file, imported module
    bindings, compiler builtins, and language keywords are offered.
    """
    if analysis.program is None or analysis.gen is None:
        return []

    lines = text.splitlines()
    if line < 0 or line >= len(lines):
        return []

    before = lines[line][:col]
    # dotted type/module paths for 'Type::' / 'mod.Type::'
    path_prefix = (
        r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")
    # value chains may mix '.' and '->': 'a.b->c->'
    member_receiver = (
        r"([A-Za-z_][A-Za-z0-9_]*"
        r"(?:(?:\.|->)[A-Za-z_][A-Za-z0-9_]*)*)")
    scoped = re.search(
        path_prefix + r"::([A-Za-z_][A-Za-z0-9_]*)?$", before)
    arrow = re.search(
        member_receiver + r"->([A-Za-z_][A-Za-z0-9_]*)?$", before)
    access = re.search(
        member_receiver + r"\.([A-Za-z_][A-Za-z0-9_]*)?$", before)

    gen = analysis.gen
    gen.current_file = analysis.path
    sites = declaration_sites(analysis.program)

    member_import = member_import_context(text, line, col)
    if member_import is not None:
        module, partial, selected = member_import
        if partial is None:
            return []
        target = gen.import_targets.get((analysis.path, module))
        if target is None:
            target = gen.module_bindings.get((analysis.path, module))
        if target is None:
            return []

        files = gen.include_closure.get(target, {target})
        return [
            completion_for_name(
                analysis, sites, name, files=files, module=target)
            for name in sorted(gen.module_exports.get(target, ()))
            if name.isidentifier() and name.startswith(partial)
            and name not in selected
        ]

    aggregate = complete_aggregate_fields(analysis, sites, text, line, col)
    if aggregate is not None:
        return aggregate

    if scoped is not None:
        return complete_scoped_members(
            analysis, sites, scoped.group(1), scoped.group(2) or "")

    if arrow is not None:
        return complete_value_members(
            analysis, sites, arrow.group(1), arrow.group(2) or "",
            line, col, through_pointer=True)

    if access is not None:
        receiver, partial = access.group(1), access.group(2) or ""
        target = gen.module_bindings.get((analysis.path, receiver))
        if target is not None:
            files = gen.include_closure.get(target, {target})
            return [
                completion_for_name(
                    analysis, sites, name, files=files, module=target)
                for name in sorted(gen.module_exports.get(target, ()))
                if name.isidentifier() and name.startswith(partial)
            ]

        return complete_value_members(
            analysis, sites, receiver, partial, line, col)

    partial_match = re.search(r"[A-Za-z_][A-Za-z0-9_]*$", before)
    partial = partial_match.group() if partial_match is not None else ""
    candidates: dict[str, Completion] = {}

    fn = enclosing_function(analysis.program, analysis.path, line + 1, col)
    if fn is not None:
        scope, local_lines = local_scope(gen, fn, line + 1, col)
        for name in local_lines:
            type_ = scope[name].type if name in scope else None
            detail = f"{name}: {type_}" if type_ is not None else None
            candidates[name] = Completion(name, "variable", detail)
        for name in (*(fn.type_params or ()), *(fn.receiver_params or ())):
            candidates[name] = Completion(name, "type", name)

    visible = gen.visible.get(analysis.path)
    names = sites if visible is None else visible
    for name in names:
        if not name.isidentifier() or name.startswith("__"):
            continue
        candidates.setdefault(
            name, completion_for_name(analysis, sites, name))

    for name in gen.builtin_names:
        if not name.startswith("__"):
            candidates.setdefault(
                name, completion_for_name(analysis, sites, name))

    for (file, binding), _target in gen.module_bindings.items():
        if file == analysis.path:
            candidates[binding] = Completion(binding, "module",
                                               f"import {binding};")

    for keyword in KEYWORD_COMPLETIONS:
        candidates.setdefault(keyword, Completion(keyword, "keyword"))

    start = partial_match.start() if partial_match is not None else len(before)
    leader = before[:start].rstrip()
    if (leader.endswith(":") or leader.endswith("->")
            or leader.endswith(" as")):
        candidates = {
            name: item for name, item in candidates.items()
            if item.kind in ("struct", "interface", "enum", "type")
            or name in TYPE_KEYWORD_COMPLETIONS
        }

    return [candidates[name] for name in sorted(candidates)
            if name.startswith(partial)]


def member_import_context(text: str, line: int,
                          col: int) -> tuple | None:
    """
    Return ``(module, partial, selected)`` when the cursor is naming a
    member inside ``import { ... } from module;``.

    The module follows the cursor, so ordinary prefix-only completion cannot
    identify it.  Reading the complete declaration also lets completion work
    in multiline import lists without asking the parser to accept the partial
    member currently being typed.
    """
    lines = text.splitlines(keepends=True)
    if line < 0 or line >= len(lines):
        return None

    offset = sum(len(source_line) for source_line in lines[:line])
    offset += min(col, len(lines[line].rstrip("\r\n")))
    pattern = re.compile(
        r"\bimport\s*\{(?P<members>.*?)\}\s*from\s+"
        r"(?P<module>[A-Za-z_][A-Za-z0-9_]*"
        r"(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*;",
        re.DOTALL,
    )

    for match in pattern.finditer(text):
        start, end = match.span("members")
        if not start <= offset <= end:
            continue

        before = text[start:offset]
        parts = before.split(",")
        current = parts[-1]
        partial_match = re.fullmatch(
            r"\s*([A-Za-z_][A-Za-z0-9_]*)?\s*", current)
        if partial_match is None:
            return (match.group("module"), None, frozenset())

        selected = frozenset(
            selected.group(1)
            for part in parts[:-1]
            if (selected := re.match(
                r"\s*([A-Za-z_][A-Za-z0-9_]*)", part)) is not None
        )
        return (match.group("module"),
                partial_match.group(1) or "", selected)

    return None


def source_offset(text: str, line: int, col: int) -> int | None:
    """Byte offset of a 0-based line/column in ``text``, or None if OOB."""
    lines = text.splitlines(keepends=True)
    if line < 0 or line >= len(lines):
        return None

    return (sum(len(source_line) for source_line in lines[:line])
            + min(col, len(lines[line].rstrip("\r\n"))))


def split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split ``text`` on ``sep`` ignoring separators inside (), [], {}."""
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(text):
        if char in "{[(":
            depth += 1
        elif char in "}])":
            depth = max(depth - 1, 0)
        elif char == sep and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def enclosing_brace(text: str, offset: int) -> int | None:
    """Index of the `{` that opens the brace group containing ``offset``."""
    depth = 0
    index = offset - 1
    while index >= 0:
        char = text[index]
        if char == "}":
            depth += 1
        elif char == "{":
            if depth == 0:
                return index
            depth -= 1
        index -= 1
    return None


def aggregate_literal_context(text: str, line: int,
                              col: int) -> tuple[int, str, frozenset] | None:
    """
    Return ``(brace_offset, partial, selected)`` when the cursor is naming
    a field inside a named aggregate literal ``{ field = ... }``.

    Positional aggregates, blocks, and import lists are ignored.  A cursor
    already past ``=`` (typing a field's value) yields None.
    """
    if member_import_context(text, line, col) is not None:
        return None

    offset = source_offset(text, line, col)
    if offset is None:
        return None

    brace = enclosing_brace(text, offset)
    if brace is None:
        return None

    # 'import {' is handled elsewhere; a block body has statements
    leader = text[:brace].rstrip()
    if re.search(r"\bimport\s*$", leader):
        return None

    inside = text[brace + 1:offset]
    depth = 0
    for char in inside:
        if char in "{[(":
            depth += 1
        elif char in "}])":
            depth = max(depth - 1, 0)
        elif char == ";" and depth == 0:
            return None

    parts = split_top_level(inside)
    current = parts[-1]
    if re.search(r"(?<![<>])=(?![>=])", current):
        return None

    partial_match = re.fullmatch(
        r"\s*([A-Za-z_][A-Za-z0-9_]*)?\s*", current)
    if partial_match is None:
        return None

    selected: set[str] = set()
    for part in parts[:-1]:
        named = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", part)
        if named is None:
            if part.strip():
                return None
            continue
        selected.add(named.group(1))

    return brace, partial_match.group(1) or "", frozenset(selected)


def aggregate_expected_type(analysis: Analysis, text: str, brace: int,
                            line: int, col: int) -> str | None:
    """
    The struct/array type an aggregate literal at ``brace`` is filling,
    from a nearby ``let`` annotation, assignment, ``return``, or nested
    ``field = {``.
    """
    from siec.codegen.aliases import expand_alias
    from siec.codegen.inference import type_info
    from siec.parser.expressions import parse_expression
    from siec.parser.stream import TokenStream

    gen = analysis.gen
    prefix = text[:brace].rstrip()

    annotated = re.search(
        r":\s*(?:const\s+)?"
        r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
        r"(?:<[^;{}]*>)?)\s*=\s*$",
        prefix)
    if annotated is not None:
        return strip_const(expand_alias(gen, annotated.group(1)))

    if re.search(r"\breturn\s*$", prefix):
        fn = enclosing_function(analysis.program, analysis.path, line + 1, col)
        if fn is not None and fn.return_type is not None:
            return strip_const(expand_alias(gen, fn.return_type))
        return None

    # 'field = {' nested inside another aggregate, not 'lhs = {'
    nested = re.search(
        r"[{,]\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*$", prefix)
    parent = enclosing_brace(text, brace) if nested is not None else None
    if nested is not None and parent is not None:
        parent_type = aggregate_expected_type(
            analysis, text, parent, line, col)
        if parent_type is not None:
            info = type_info(gen, parent_type)
            if info is not None and info.fields:
                field = next((f for f in info.fields
                              if f.name == nested.group(1)), None)
                if field is not None:
                    return strip_const(expand_alias(gen, field.type))

    assigned = re.search(
        r"((?:\*\s*)?(?:self|[A-Za-z_][A-Za-z0-9_]*)"
        r"(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*=\s*$",
        prefix)
    if assigned is not None:
        fn = enclosing_function(analysis.program, analysis.path, line + 1, col)
        scope = (local_scope(gen, fn, line + 1, col)[0]
                 if fn is not None else {})
        try:
            expr = parse_expression(TokenStream(lex(assigned.group(1))))
            return strip_const(expand_alias(
                gen, hover_expr_type(gen, expr, scope) or "")) or None
        except (TypeError, NameError, SyntaxError, KeyError, IndexError,
                RuntimeError):
            return None

    return None


def complete_aggregate_fields(analysis: Analysis, sites: dict, text: str,
                              line: int, col: int) -> list[Completion] | None:
    """
    Field names for a named aggregate literal, or None when the cursor is
    not naming one.
    """
    from siec.codegen.generics import split_generic
    from siec.codegen.inference import type_info

    context = aggregate_literal_context(text, line, col)
    if context is None:
        return None

    brace, partial, selected = context
    type_name = aggregate_expected_type(analysis, text, brace, line, col)
    if not type_name:
        return []

    gen = analysis.gen
    fn = enclosing_function(analysis.program, analysis.path, line + 1, col)
    previous = gen.checking_function
    gen.checking_function = fn
    try:
        info = type_info(gen, type_name)
        if info is None and (parts := split_generic(type_name)) is not None:
            info = gen.generic_structs.get(parts[0])
        if info is None or not info.fields:
            return []

        candidates: dict[str, Completion] = {}
        for field_ in info.fields:
            name = field_.name
            if (not name.isidentifier() or name.startswith("#")
                    or name in selected or not name.startswith(partial)):
                continue
            if field_.is_private and not gen.can_access_private_field(type_name):
                continue
            finding = field_finding(analysis, sites, type_name, name)
            detail = finding.text if finding is not None else \
                f"{name}: {field_.type}"
            candidates[name] = Completion(name, "field", detail)
    finally:
        gen.checking_function = previous

    return [candidates[name] for name in sorted(candidates)]


def complete_scoped_members(analysis: Analysis, sites: dict, base: str,
                            partial: str) -> list[Completion]:
    """Complete enum members or type methods after ``Type::``."""
    from siec.codegen.aliases import expand_alias

    gen = analysis.gen
    type_name = base.rsplit(".", 1)[-1] if "." in base else base
    type_name = strip_const(expand_alias(gen, type_name))
    candidates: dict[str, Completion] = {}

    if (info := gen.enums.get(type_name)) is not None:
        for name, value in info.members.items():
            if name.startswith(partial):
                candidates[name] = Completion(
                    name, "enumMember", f"{type_name}::{name} = {value}")
        return [candidates[name] for name in sorted(candidates)]

    method_names = set(gen.generic_receiver_methods)
    method_names.update(method for _receiver, method in gen.generic_methods)
    for declared in sites:
        if "::" in declared:
            method_names.add(declared.rpartition("::")[2])

    for name in method_names:
        if not name.startswith(partial):
            continue
        finding = method_finding(analysis, sites, type_name, name)
        if finding is not None:
            candidates.setdefault(
                name, Completion(name, "method", finding.text))

    return [candidates[name] for name in sorted(candidates)]


def complete_value_members(analysis: Analysis, sites: dict, receiver: str,
                           partial: str, line: int,
                           col: int, through_pointer: bool = False
                           ) -> list[Completion]:
    """Complete the fields and eligible methods of a typed expression."""
    from siec.ast import UnaryOp
    from siec.codegen.generics import split_generic
    from siec.parser.expressions import parse_expression
    from siec.parser.stream import TokenStream

    gen = analysis.gen
    fn = enclosing_function(analysis.program, analysis.path, line + 1, col)
    scope = local_scope(gen, fn, line + 1, col)[0] if fn is not None else {}

    try:
        expr = parse_expression(TokenStream(lex(receiver)))
        # 'p->' reaches through a pointer the same way the parser does
        if through_pointer:
            expr = UnaryOp("*", expr)
        receiver_type = hover_expr_type(gen, expr, scope)
    except (TypeError, NameError, SyntaxError, KeyError, IndexError,
            RuntimeError):
        return []

    if receiver_type is None:
        return []

    base = strip_const(strip_reference(strip_const(receiver_type)))
    candidates: dict[str, Completion] = {}

    info = gen.structs.get(base)
    if info is None and (parts := split_generic(base)) is not None:
        info = gen.generic_structs.get(parts[0])

    fields = info.fields if info is not None and info.fields else ()
    for field_ in fields:
        if field_.name.startswith(partial):
            finding = field_finding(analysis, sites, base, field_.name)
            detail = finding.text if finding is not None else \
                f"{field_.name}: {field_.type}"
            candidates[field_.name] = Completion(
                field_.name, "field", detail)

    method_names = set(gen.generic_receiver_methods)
    method_names.update(method for _receiver, method in gen.generic_methods)
    for declared in sites:
        if "::" in declared:
            method_names.add(declared.rpartition("::")[2])

    for name in method_names:
        if not name.startswith(partial):
            continue
        finding = method_finding(analysis, sites, base, name)
        if finding is not None:
            candidates.setdefault(
                name, Completion(name, "method", finding.text))

    return [candidates[name] for name in sorted(candidates)]


def completion_for_name(analysis: Analysis, sites: dict, name: str,
                        files=None, module: str | None = None) -> Completion:
    """Classify a compiler-visible name and provide a concise declaration."""
    gen = analysis.gen
    lookup = name
    if module is not None:
        lookup = gen.module_type_symbols.get((module, name), name)
    else:
        member = gen.member_targets.get((analysis.path, name))
        if member is not None:
            target, original = member
            lookup = gen.module_type_symbols.get((target, original), original)
        else:
            lookup = gen.local_type_symbols.get((analysis.path, name), name)

    found = [(kind, decl) for kind, decl in sites.get(lookup, ())
             if files is None or decl.file in files]
    if not found:
        return Completion(name, "text")

    kind, decl = found[0]
    if kind == "function":
        details = []
        for _, fn in found:
            if (shown := signature(fn)) not in details:
                details.append(shown)
        detail = details[0]
        if len(details) > 1:
            detail += f" (+{len(details) - 1} overloads)"
        return Completion(name, "function", detail)

    if kind == "struct":
        item_kind = "interface" if decl.is_interface else "struct"
        return Completion(name, item_kind, struct_text(decl))

    if kind == "enum":
        return Completion(name, "enum", enum_text(decl))

    if kind == "constant":
        item_kind = "function" if decl.is_macro else "constant"
        return Completion(name, item_kind,
                          source_line(analysis, decl.file, decl.line))

    if kind == "variable":
        return Completion(name, "variable", f"{name}: {decl.type}")

    if kind == "alias":
        return Completion(name, "type", f"{name} = {decl.type}")

    return Completion(name, "text")


def inspect(analysis: Analysis, text: str, line: int, col: int) -> Finding | None:
    """
    Resolve the name at a 0-based position of the unit's root buffer:
    hover text and declaration sites, or None when nothing resolves.

    The chain types through the compiler's own inference against the
    cached generator, so what hover says is what the compiler knows.
    """
    if analysis.program is None or analysis.gen is None:
        return None

    try:
        tokens = lex(text)
    except SyntaxError:
        return None

    chain = token_chain(tokens, line, col, text)
    if chain is None:
        return None

    parts, seps, token, following = chain
    gen = analysis.gen
    gen.current_file = analysis.path

    fn = enclosing_function(analysis.program, analysis.path,
                            token.line, token.col)
    scope, lines = (local_scope(gen, fn, token.line, token.col)
                    if fn else ({}, {}))
    sites = declaration_sites(analysis.program)

    try:
        return resolve_chain(analysis, sites, scope, lines, parts, seps,
                             following)
    except (TypeError, NameError, SyntaxError, KeyError, IndexError,
            RuntimeError):
        return None


def resolve_chain(analysis: Analysis, sites: dict, scope: dict, lines: dict,
                  parts: list, seps: list, following) -> Finding | None:
    """
    Resolve a name chain: a bare name in scope order, an 'E::M' member or
    'S::m' method through its base type, a dotted chain through module
    bindings when its prefix names one and through the receiver
    expression's inferred type otherwise.
    """
    gen = analysis.gen
    name = parts[-1]

    # a chain naming a module is the module itself: an import's path, or
    # the prefix a qualified use reaches through
    spelling = spell(parts, seps)
    module = gen.module_bindings.get((analysis.path, spelling))
    if module is not None:
        return Finding(text=f"import {spelling};", targets=[(module, 1)])

    if len(parts) == 1 and name not in ("self",):
        if name in lines:
            return resolve_local(analysis, scope, lines, name)

        return resolve_name(analysis, sites, name, None)

    if len(parts) == 1:
        return resolve_local(analysis, scope, lines, name)

    if seps[-1] == "::":
        base = spell(parts[:-1], seps[:-1])
        return resolve_scoped(analysis, sites, scope, base, name)

    # the longest bound module prefix claims the chain; what is left
    # past it is the member itself
    for split in range(len(parts) - 1, 0, -1):
        prefix = spell(parts[:split], seps[:split - 1])
        target = gen.module_bindings.get((analysis.path, prefix))
        if target is not None and split == len(parts) - 1:
            files = getattr(analysis.program, "include_closure",
                            {}).get(target, {target})
            return resolve_name(analysis, sites, name, files)

        if target is not None:
            break

    # a member chain: the receiver types through the compiler's
    # inference, the final link reading as its field or method
    receiver = spell(parts[:-1], seps[:-1])
    return resolve_member(analysis, sites, scope, receiver, name, following)


def spell(parts: list, seps: list) -> str:
    """
    Rejoin a chain's spelling from its parts and separators.
    """
    return parts[0] + "".join(s + p for s, p in zip(seps, parts[1:]))


def resolve_local(analysis: Analysis, scope: dict, lines: dict,
                  name: str) -> Finding | None:
    """
    A body's own name: its type when known, its declaring line either way.
    """
    if name not in lines:
        return None

    at = lines[name]
    if name in scope and scope[name].type is not None:
        text = f"{name}: {scope[name].type}"
    else:
        text = source_line(analysis, analysis.path, at) or name

    return Finding(text, [(analysis.path, at)])


def resolve_name(analysis: Analysis, sites: dict, name: str,
                 files) -> Finding | None:
    """
    A top-level name: constants, globals, functions with every overload's
    signature, and type declarations, restricted to a module's files when
    the chain came through its binding.
    """
    gen = analysis.gen
    found = [(kind, decl) for kind, decl in sites.get(name, ())
             if files is None or decl.file in files]
    if not found:
        return None

    targets = [
        (decl.file, decl.line)
        for _, decl in found
        if navigable_file(decl.file)
    ]

    kind = found[0][0]
    if kind == "function":
        from siec.codegen.generics import template_identity, template_return
        from siec.codegen.overloads import overload_key

        groups = {}
        for entry in found:
            decl = entry[1]
            if decl.type_params is not None:
                key = (template_identity(decl), template_return(decl))
            else:
                key = (overload_key(decl.params), decl.return_type)
            groups.setdefault(key, []).append(entry)

        found = [
            entry
            for declarations in groups.values()
            for entry in (
                [candidate for candidate in declarations
                 if candidate[1].is_override]
                or declarations
            )
        ]
        texts = []
        for _, decl in found:
            if (sig := signature(decl)) not in texts:
                texts.append(sig)

        targets = [
            (decl.file, decl.line)
            for _, decl in found
            if navigable_file(decl.file)
        ]
        return Finding("\n".join(texts), targets)

    decl = found[0][1]
    if kind == "struct":
        return Finding(struct_text(decl), targets)

    if kind == "enum":
        return Finding(enum_text(decl), targets)

    if kind == "variable":
        symbol = gen.resolve_symbol(name)
        if symbol in gen.globals:
            return Finding(f"{name}: {gen.globals[symbol]}", targets)

    # constants and aliases read best as declared
    text = source_line(analysis, decl.file, decl.line)
    return Finding(text or name, targets)


def resolve_scoped(analysis: Analysis, sites: dict, scope: dict, base: str,
                   name: str) -> Finding | None:
    """
    An 'E::M' enum member or 'S::m' method reference: the base names a
    type, dotted through a module binding when spelled so.
    """
    from siec.codegen.aliases import expand_alias

    gen = analysis.gen

    if "." in base:
        base = base.rsplit(".", 1)[1]

    base = strip_const(expand_alias(gen, base))

    if (info := gen.enums.get(base)) is not None:
        node = next((decl for kind, decl in sites.get(base, ())
                     if kind == "enum"), None)
        variant = next((v for v in node.members if v.name == name), None) \
            if node else None

        value = info.members.get(name)
        text = f"{base}::{name}" + (f" = {value}" if value is not None else "")
        if node is not None and navigable_file(node.file):
            at = variant.line if variant is not None and variant.line else node.line
            return Finding(text, [(node.file, at)])

        return Finding(text)

    return method_finding(analysis, sites, base, name)


def resolve_member(analysis: Analysis, sites: dict, scope: dict,
                   receiver: str, name: str, following) -> Finding | None:
    """
    The final link of a member chain: the receiver's inferred type hands
    out the field or method the name selects.
    """
    from siec.parser.expressions import parse_expression
    from siec.parser.stream import TokenStream

    gen = analysis.gen
    expr = parse_expression(TokenStream(lex(receiver)))
    recv_type = hover_expr_type(gen, expr, scope)
    if recv_type is None:
        return None

    base = strip_const(strip_reference(strip_const(recv_type)))

    # a call selects a method; otherwise the field wins, methods (a bare
    # reference) trying after
    if following != "(":
        if (finding := field_finding(analysis, sites, base, name)) is not None:
            return finding

    return (method_finding(analysis, sites, base, name)
            or field_finding(analysis, sites, base, name))


def hover_expr_type(gen: CodeGenerator, expr, scope: dict) -> str | None:
    """
    Type a hover receiver through the compiler, with one tooling fallback:
    a call inside an unstamped generic method may return a type over that
    method's still-free receiver parameters.
    """
    from siec.ast import Call
    from siec.codegen.inference import expr_sie_type

    receiver_type = None
    method = None
    if isinstance(expr, Call) and "." in expr.name:
        from siec.parser.expressions import parse_expression
        from siec.parser.stream import TokenStream

        receiver, _, method = expr.name.rpartition(".")
        receiver_expr = parse_expression(TokenStream(lex(receiver)))
        receiver_type = hover_expr_type(gen, receiver_expr, scope)

        # Resolving a method normally would try to stamp 'List<T>::m'
        # while T is still free. Read its template instead, before that
        # failed instantiation can mutate the generator's method tables.
        if (receiver_type is not None
                and contains_free_type(gen, receiver_type)):
            if (found := template_method_return(
                    gen, receiver_type, method)) is not None:
                return found

    try:
        if (found := expr_sie_type(gen, expr, scope)) is not None:
            return found
    except (TypeError, NameError, RuntimeError):
        pass

    if receiver_type is None or method is None:
        return None

    return template_method_return(gen, receiver_type, method)


def contains_free_type(gen: CodeGenerator, spelling: str) -> bool:
    """Whether a type spelling contains an unresolved template name."""
    from siec.codegen.generics import split_generic
    from siec.codegen.interfaces import free_name

    spelling = strip_const(strip_reference(spelling))
    while spelling.endswith("*"):
        spelling = spelling[:-1]
    if spelling.endswith("[]"):
        return contains_free_type(gen, spelling[:-2])

    parts = split_generic(spelling)
    if parts is not None:
        return any(contains_free_type(gen, arg) for arg in parts[1])

    return free_name(gen, spelling)


def template_method_return(gen: CodeGenerator, receiver_type: str,
                           method: str) -> str | None:
    """
    A generic receiver template's return before its receiver is concrete.
    Return it only when every matching overload agrees on the spelling.
    """
    from siec.codegen.generics import split_generic, substitute, unify

    base = strip_const(strip_reference(receiver_type))
    parts = split_generic(base)
    if parts is None and base.endswith("[]"):
        parts = ("[]", [base[:-2]])

    entries = []
    if parts is not None:
        entries.extend(
            (template, dict(zip(template.receiver_params, parts[1])))
            for template in gen.generic_methods.get((parts[0], method), ())
        )

    if not entries:
        for template in gen.generic_receiver_methods.get(method, ()):
            mapping = {}
            unify(template.receiver, base, template.receiver_params, mapping)
            if all(param in mapping for param in template.receiver_params):
                entries.append((template, mapping))

    returns = {
        strip_reference(substitute(template.return_type, mapping))
        for template, mapping in entries
        if template.return_type is not None
    }
    return returns.pop() if len(returns) == 1 else None


def field_finding(analysis: Analysis, sites: dict, base: str,
                  name: str) -> Finding | None:
    """
    A struct field: its declared type, sited at its line in the struct's
    file - the template's for a generic instantiation.
    """
    from siec.codegen.generics import split_generic

    gen = analysis.gen
    info = gen.structs.get(base)
    if info is None or not info.fields:
        return None

    found = next((f for f in info.fields if f.name == name), None)
    if found is None:
        return None

    node = next((decl for kind, decl in sites.get(base, ())
                 if kind == "struct"), None)
    if node is None and (parts := split_generic(base)) is not None:
        node = gen.generic_structs.get(parts[0])

    targets = []
    if node is not None and navigable_file(node.file):
        targets = [(node.file, found.line or node.line)]

    return Finding(f"{name}: {found.type}", targets)


def method_finding(analysis: Analysis, sites: dict, base: str,
                   name: str) -> Finding | None:
    """
    A method on a base type: every overload's signature, from the
    concrete declarations and the generic struct's templates alike.
    """
    from siec.codegen.generics import split_generic, unify
    from siec.codegen.interfaces import constraints_hold, free_name
    from siec.codegen.methods import resolve_method, select_method_overrides

    gen = analysis.gen

    def unresolved(spelling: str) -> bool:
        """Whether an editor-side type still contains a free placeholder."""
        spelling = strip_const(strip_reference(spelling))
        if spelling.endswith("[]"):
            return unresolved(spelling[:-2])
        if (generic := split_generic(spelling)) is not None:
            return any(unresolved(arg) for arg in generic[1])
        return free_name(gen, spelling)

    def eligible(template, mapping: dict) -> bool:
        # During inspection, a receiver may still contain its enclosing
        # function's placeholder. Its bounds cannot be decided until that
        # function is instantiated, so keep the conditional declaration
        # visible just as resolution will then do.
        return (constraints_hold(
                    gen, template.receiver_constraints,
                    mapping, template.file)
                or any(unresolved(actual) for actual in mapping.values()))

    try:
        # Inspection reads the completed compiler index. It must not stamp a
        # previously unused generic method after semantic checking and LLVM
        # lowering have ended; the template lookup below supplies its hover.
        symbol = resolve_method(gen, base, name, specialize=False)
    except (TypeError, NameError):
        symbol = None

    exact = [decl for kind, decl in sites.get(f"{base}::{name}", ())
             if kind == "function" and decl.receiver_params is None]
    if exact:
        overrides = [node for node in exact if node.is_override]
        nodes = overrides or exact
    else:
        parts = split_generic(base)
        if parts is None and base.endswith("[]"):
            parts = ("[]", [base[:-2]])

        entries = []
        if parts is not None:
            for template in gen.generic_methods.get((parts[0], name), ()):
                mapping = dict(zip(template.receiver_params or (), parts[1]))
                if eligible(template, mapping):
                    entries.append((template, mapping))

        for template in gen.generic_receiver_methods.get(name, ()):
            mapping = {}
            try:
                unify(template.receiver, base,
                      template.receiver_params, mapping)
            except TypeError:
                continue
            if (all(param in mapping for param in template.receiver_params)
                    and eligible(template, mapping)):
                entries.append((template, mapping))

        try:
            entries = select_method_overrides(gen, base, name, entries)
        except TypeError:
            entries = []
        nodes = [template for template, _ in entries]

    unique = []
    seen = set()
    for node in nodes:
        if id(node) not in seen:
            unique.append(node)
            seen.add(id(node))
    nodes = unique

    if not nodes and symbol is None:
        return None

    texts = []
    for node in nodes:
        if (sig := signature(node)) not in texts:
            texts.append(sig)

    # a resolved symbol with no declaration in sight still has its
    # registered types to show
    if not texts and symbol is not None:
        from siec.codegen.overloads import overload_candidates

        for sibling in overload_candidates(gen, symbol):
            params = ", ".join(gen.param_types.get(sibling, []))
            ret = gen.return_types.get(sibling)
            texts.append(f"fn {base}::{name}({params})"
                         + (f" -> {ret}" if ret else ""))

    targets = [
        (node.file, node.line)
        for node in nodes
        if navigable_file(node.file)
    ]
    return Finding("\n".join(texts), targets) if texts else None


def create_server():
    """
    Build the pygls server: diagnostics published on open, change (a
    beat after the last keystroke), and save; document symbols from the
    outline; hover, completion, and go-to-definition from the last good
    analysis.
    Initialization options may carry {"includePaths": [...]}.
    """
    from lsprotocol import types
    from pygls.lsp.server import LanguageServer
    from pygls.uris import from_fs_path, to_fs_path

    server = LanguageServer("sie-lsp", "0.1.0")

    kinds = {"function": types.SymbolKind.Function,
             "method": types.SymbolKind.Method,
             "struct": types.SymbolKind.Struct,
             "interface": types.SymbolKind.Interface,
             "enum": types.SymbolKind.Enum,
             "constant": types.SymbolKind.Constant,
             # a macro substitutes rather than stores; an editor's outline
             # has no kind of its own for it, so it shows as a function
             "macro": types.SymbolKind.Function,
             "variable": types.SymbolKind.Variable,
             "alias": types.SymbolKind.Class}

    completion_kinds = {
        "text": types.CompletionItemKind.Text,
        "keyword": types.CompletionItemKind.Keyword,
        "module": types.CompletionItemKind.Module,
        "function": types.CompletionItemKind.Function,
        "method": types.CompletionItemKind.Method,
        "field": types.CompletionItemKind.Field,
        "struct": types.CompletionItemKind.Struct,
        "interface": types.CompletionItemKind.Interface,
        "enum": types.CompletionItemKind.Enum,
        "enumMember": types.CompletionItemKind.EnumMember,
        "constant": types.CompletionItemKind.Constant,
        "variable": types.CompletionItemKind.Variable,
        "type": types.CompletionItemKind.Class,
    }

    workspace = {"root": None, "extra": [], "debug": False}
    outlines: dict[str, list[Symbol]] = {}
    analyses: dict[str, Analysis] = {}
    loaded_files: dict[str, frozenset[str]] = {}
    analysis_cache = UnitAnalysisCache()
    path_cache = SearchPathCache()
    # Compiler and loader caches are mutable and intentionally serialized.
    # A dedicated worker keeps their entire synchronous pipeline off the LSP
    # event loop without introducing cross-compilation races.
    analysis_executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="sie-lsp-analysis")

    def log_failure(context: str, error: Exception | None = None) -> None:
        """Log a stable message, adding exception detail only in debug mode."""
        message = f"sie-lsp: {context} failed unexpectedly"
        if workspace["debug"] and error is not None:
            detail = "".join(traceback.format_exception(
                type(error), error, error.__traceback__))
            message += f"\n{detail}"
        try:
            server.window_log_message(types.LogMessageParams(
                type=types.MessageType.Error, message=message))
        except Exception:
            # Logging may be unavailable during initialization or shutdown.
            pass

    def log_invalid_options(message: str) -> None:
        """Report ignored client configuration without failing initialize."""
        try:
            server.window_log_message(types.LogMessageParams(
                type=types.MessageType.Warning,
                message=f"sie-lsp: {message}"))
        except Exception:
            pass

    def guarded(context: str, fallback):
        """Keep malformed requests and handler bugs behind the LSP boundary."""
        def decorate(function):
            def default():
                return fallback() if callable(fallback) else fallback

            if iscoroutinefunction(function):
                @wraps(function)
                async def asynchronous(*args, **kwargs):
                    try:
                        return await function(*args, **kwargs)
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        log_failure(context, error)
                        return default()
                return asynchronous

            @wraps(function)
            def synchronous(*args, **kwargs):
                try:
                    return function(*args, **kwargs)
                except Exception as error:
                    log_failure(context, error)
                    return default()
            return synchronous
        return decorate

    def document_path(uri: str) -> Path:
        return Path(to_fs_path(uri)).resolve()

    def line_range(line: int | None, doc) -> types.Range:
        # underline the whole 1-based line; the parser tracks no columns
        at = min((line or 1) - 1, max(len(doc.lines) - 1, 0))
        width = len(doc.lines[at].rstrip("\n")) if doc.lines else 0
        return types.Range(start=types.Position(line=at, character=0),
                           end=types.Position(line=at, character=width))

    def resolve(uri: str, position) -> Finding | None:
        # resolve against the last analysis that parsed; the buffer may
        # be ahead of it, so the tokens come fresh from the document
        analysis = analyses.get(uri)
        if analysis is None:
            return None

        doc = server.workspace.get_text_document(uri)
        return inspect(analysis, doc.source, position.line, position.character)

    def open_overlays() -> dict[str, str]:
        """Snapshot every open buffer on the event-loop thread."""
        return {str(document_path(doc.uri)): doc.source
                for doc in server.workspace.text_documents.values()}

    def compile_snapshot(path: Path, root: Path | None,
                         extra: tuple[str, ...],
                         overlays: dict[str, str]) -> Analysis:
        """Resolve paths and compile one immutable editor snapshot."""
        paths = search_paths(root, list(extra), path.parent, path_cache)
        return compile_unit(path, paths, overlays, analysis_cache)

    async def validate(uri: str) -> None:
        doc = server.workspace.get_text_document(uri)
        path = document_path(uri)
        revision = (getattr(doc, "version", None), doc.source)

        # every open buffer overlays its file, so cross-file edits
        # analyze as typed, saved or not
        overlays = open_overlays()
        analysis = await asyncio.get_running_loop().run_in_executor(
            analysis_executor,
            compile_snapshot,
            path,
            workspace["root"],
            tuple(workspace["extra"]),
            overlays,
        )

        # Cancellation cannot stop Python already executing in a thread, but
        # it cancels this coroutine immediately. These guards also cover the
        # narrow race where a result and a document change reach the loop in
        # the same turn. A dependency edit can change another overlay without
        # changing this document's version, so compare the complete snapshot.
        if debounce.pending.get(uri) is not asyncio.current_task():
            return
        if uri not in server.workspace.text_documents:
            return
        current = server.workspace.get_text_document(uri)
        if ((getattr(current, "version", None), current.source) != revision
                or open_overlays() != overlays):
            debounce.schedule(uri)
            return

        loaded_files[uri] = analysis.files
        if analysis.program is not None:
            analyses[uri] = analysis

        report = analysis.report
        diagnostics = []
        if report is not None:
            message, line = report.message, report.line

            # an error in another file surfaces here under that file's
            # name, at the top; its own buffer shows the exact line
            if report.file != str(path):
                where = f" at line {line}" if line is not None else ""
                message = f"{Path(report.file).name}{where}: {message}"
                line = None

            diagnostics = [types.Diagnostic(
                range=line_range(line, doc),
                message=message,
                severity=types.DiagnosticSeverity.Error,
                source="siec")]

        for item in analysis.diagnostics:
            if item.severity != "warning":
                continue

            message, line = item.message, item.line
            if item.file and item.file != str(path):
                where = f" at line {line}" if line is not None else ""
                message = f"{Path(item.file).name}{where}: {message}"
                line = None

            diagnostics.append(types.Diagnostic(
                range=line_range(line, doc),
                message=message,
                severity=types.DiagnosticSeverity.Warning,
                source="siec",
                code=item.code,
            ))

        server.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics))

        # A debounced analysis may finish after the client's automatic
        # request for this edit. Ask capable clients to repaint the branch
        # choices now that they match the compiler's latest state.
        capabilities = getattr(server, "client_capabilities", None)
        workspace_caps = getattr(capabilities, "workspace", None)
        semantic_caps = getattr(workspace_caps, "semantic_tokens", None)
        if getattr(semantic_caps, "refresh_support", False):
            server.workspace_semantic_tokens_refresh(None)

    def validation_failed(uri: str, error: Exception) -> None:
        """Invalidate stale editor state and publish one sanitized failure."""
        analyses.pop(uri, None)
        outlines.pop(uri, None)
        loaded_files.pop(uri, None)
        log_failure("document analysis", error)

        try:
            doc = server.workspace.get_text_document(uri)
            diagnostic = types.Diagnostic(
                range=line_range(None, doc),
                message=("Sie analysis failed unexpectedly; see the language "
                         "server log for details"),
                severity=types.DiagnosticSeverity.Error,
                source="siec")
            server.text_document_publish_diagnostics(
                types.PublishDiagnosticsParams(
                    uri=uri, diagnostics=[diagnostic]))
        except Exception as reporting_error:
            log_failure("diagnostic reporting", reporting_error)

    debounce = _ValidationDebouncer(validate, on_error=validation_failed)
    analysis_stopped = False

    def stop_analysis() -> None:
        """Retire editor jobs and stop accepting compiler work."""
        nonlocal analysis_stopped
        if analysis_stopped:
            return
        analysis_stopped = True
        debounce.cancel_all()
        analysis_executor.shutdown(wait=False, cancel_futures=True)

    def affected(uri: str) -> set[str]:
        """The changed document and every open unit whose inputs contain it."""
        return {uri, *dependent_uris(loaded_files, document_path(uri))}

    async def validate_affected(uri: str) -> None:
        if analysis_stopped:
            return
        await asyncio.gather(*(
            debounce.run_now(affected_uri)
            for affected_uri in affected(uri)
        ))

    def schedule_affected(uri: str) -> None:
        if analysis_stopped:
            return
        for affected_uri in affected(uri):
            debounce.schedule(affected_uri)

    @server.feature(types.INITIALIZE)
    @guarded("initialize request", None)
    def initialize(params: types.InitializeParams) -> None:
        root_uri = getattr(params, "root_uri", None)
        if root_uri is not None:
            if isinstance(root_uri, str):
                workspace["root"] = Path(to_fs_path(root_uri))
            else:
                log_invalid_options("ignoring non-string workspace root URI")

        raw_options = getattr(params, "initialization_options", None)
        if raw_options is None:
            options = {}
        elif isinstance(raw_options, Mapping):
            options = raw_options
        else:
            options = {}
            log_invalid_options("initializationOptions must be an object")

        raw_paths = options.get("includePaths", [])
        if (isinstance(raw_paths, list)
                and all(isinstance(path, str) and "\0" not in path
                        for path in raw_paths)):
            workspace["extra"] = list(raw_paths)
        else:
            workspace["extra"] = []
            log_invalid_options(
                "initializationOptions.includePaths must be an array of paths")

        raw_debug = options.get("debug", False)
        if isinstance(raw_debug, bool):
            workspace["debug"] = raw_debug
        else:
            workspace["debug"] = False
            log_invalid_options(
                "initializationOptions.debug must be a boolean")

    @server.feature(types.SHUTDOWN)
    def shutdown(*_args) -> None:
        stop_analysis()

    @server.feature(types.TEXT_DOCUMENT_DID_OPEN)
    @guarded("didOpen notification", None)
    async def did_open(params: types.DidOpenTextDocumentParams) -> None:
        await validate_affected(params.text_document.uri)

    @server.feature(types.TEXT_DOCUMENT_DID_SAVE)
    @guarded("didSave notification", None)
    async def did_save(params: types.DidSaveTextDocumentParams) -> None:
        await validate_affected(params.text_document.uri)

    @server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
    @guarded("didChange notification", None)
    async def did_change(params: types.DidChangeTextDocumentParams) -> None:
        # let the keystrokes settle, then recompile; a newer change
        # cancels a wait still in flight
        schedule_affected(params.text_document.uri)

    @server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
    @guarded("didClose notification", None)
    def did_close(params: types.DidCloseTextDocumentParams) -> None:
        uri = params.text_document.uri
        dependants = set(dependent_uris(loaded_files, document_path(uri)))
        dependants.discard(uri)
        debounce.cancel(uri)
        outlines.pop(uri, None)
        analyses.pop(uri, None)
        loaded_files.pop(uri, None)
        # Cache mutation stays ordered with compilation on the same worker.
        if not analysis_stopped:
            analysis_executor.submit(
                analysis_cache.discard, document_path(uri))
        server.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(uri=uri, diagnostics=[]))
        for dependant in dependants:
            debounce.schedule(dependant)

    @server.feature(types.TEXT_DOCUMENT_HOVER)
    @guarded("hover request", None)
    def hover(params: types.HoverParams) -> types.Hover | None:
        finding = resolve(params.text_document.uri, params.position)
        if finding is None:
            return None

        content = types.MarkupContent(kind=types.MarkupKind.Markdown,
                                      value=f"```sie\n{finding.text}\n```")
        return types.Hover(contents=content)

    @server.feature(
        types.TEXT_DOCUMENT_COMPLETION,
        types.CompletionOptions(trigger_characters=[".", ":", ">", "{", ","]))
    @guarded("completion request", list)
    def completion(params: types.CompletionParams) -> list:
        uri = params.text_document.uri
        analysis = analyses.get(uri)
        if analysis is None:
            return []

        doc = server.workspace.get_text_document(uri)
        context = params.context
        trigger = context.trigger_character if context is not None else None
        if trigger in ("{", ",") and member_import_context(
                doc.source, params.position.line,
                params.position.character) is None \
                and aggregate_literal_context(
                    doc.source, params.position.line,
                    params.position.character) is None:
            return []

        items = complete(analysis, doc.source, params.position.line,
                         params.position.character)
        return [types.CompletionItem(
            label=item.label,
            kind=completion_kinds[item.kind],
            detail=item.detail,
        ) for item in items]

    @server.feature(
        types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
        types.SemanticTokensLegend(token_types=["comment"],
                                   token_modifiers=[]))
    @guarded("semantic tokens request", lambda: types.SemanticTokens(data=[]))
    def semantic_tokens(
            params: types.SemanticTokensParams) -> types.SemanticTokens:
        analysis = analyses.get(params.text_document.uri)
        if analysis is None:
            return types.SemanticTokens(data=[])

        doc = server.workspace.get_text_document(params.text_document.uri)
        return types.SemanticTokens(
            data=inactive_semantic_tokens(analysis, doc.source))

    @server.feature(types.TEXT_DOCUMENT_DEFINITION)
    @guarded("definition request", list)
    def definition(params: types.DefinitionParams) -> list:
        finding = resolve(params.text_document.uri, params.position)
        if finding is None:
            return []

        # a zero-width range at the line's start: the jump target
        return [types.Location(
            uri=from_fs_path(file),
            range=types.Range(start=types.Position(line=line - 1, character=0),
                              end=types.Position(line=line - 1, character=0)))
                for file, line in finding.targets if line]

    @server.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
    @guarded("document symbol request", list)
    def document_symbol(params: types.DocumentSymbolParams) -> list:
        uri = params.text_document.uri
        doc = server.workspace.get_text_document(uri)

        symbols = outline(doc.source)
        if symbols is None:
            symbols = outlines.get(uri, [])
        else:
            outlines[uri] = symbols

        return [types.DocumentSymbol(name=s.name, kind=kinds[s.kind],
                                     range=line_range(s.line, doc),
                                     selection_range=line_range(s.line, doc))
                for s in symbols]

    return server


def main() -> int:
    """
    Start the language server over stdio.
    """
    try:
        import pygls  # noqa: F401
    except ImportError:
        print("sie-lsp needs the 'pygls' package: pip install siec[lsp]",
              file=sys.stderr)
        return 1

    create_server().start_io()
    return 0


if __name__ == "__main__":
    sys.exit(main())
