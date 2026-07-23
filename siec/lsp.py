"""The Sie language server, speaking LSP over stdio.

The server reuses the compiler's front end wholesale: diagnostics come
from running the loader and codegen over the editor's buffers, and the
document outline from the parser's AST. Analysis compiles each file as
its own unit, imports declaring only, like '-c': the edited file's
errors surface without emitting every imported module behind it.

'sie-lsp' starts it; the 'editors/' directory holds the VSCode and
Helix setups that connect it to '.sie' files.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

from siec.cli import error_parts
from siec.codegen import codegen
from siec.lexer import lex
from siec.loader import load_program
from siec.parser import parse


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
class Symbol:
    """
    One outline entry: a top-level declaration's name, kind, and 1-based
    line. Kinds: 'function', 'method', 'struct', 'interface', 'enum',
    'constant', 'variable', 'alias'.
    """
    name: str
    kind: str
    line: int


def search_paths(root: Path | None, extra: list[str]) -> list[Path]:
    """
    The include path for analysis: the configured directories first, then
    the workspace's 'packages/*/src' trees when it has them, and the root
    itself with its 'lib/', mirroring the compiler's own search.
    """
    paths = [Path(p) for p in extra]

    if root is not None:
        paths.extend(sorted(root.glob("packages/*/src")))
        paths.extend((root, root / "lib"))

    return paths


def analyze(path: Path, include_paths: list[Path],
            overlays: dict[str, str] | None = None) -> Report | None:
    """
    Compile one file as its own unit, returning the first compile error
    or None when it is clean.

    Overlays stand in for on-disk contents, so unsaved edits analyze
    live; nothing is emitted to native code.
    """
    path = path.resolve()
    paths = [*include_paths, path.parent / "lib"]

    try:
        program = load_program([path], paths, overlays=overlays)
        codegen(program, str(path), define_imports=False)
    except (SyntaxError, TypeError, NameError, FileNotFoundError) as error:
        file, line, message = error_parts(error)
        return Report(file or str(path), line, message)

    return None


def outline(text: str) -> list[Symbol] | None:
    """
    The text's top-level declarations in source order, or None when it
    does not parse - the caller keeps the last good outline then.
    """
    try:
        program = parse(lex(text))
    except (SyntaxError, TypeError, NameError):
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
            symbols.append(Symbol(const.name, "constant", const.line))

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


def create_server():
    """
    Build the pygls server: diagnostics published on open, change (a
    beat after the last keystroke), and save; document symbols from the
    outline. Initialization options may carry {"includePaths": [...]}.
    """
    import asyncio

    from lsprotocol import types
    from pygls.lsp.server import LanguageServer
    from pygls.uris import to_fs_path

    server = LanguageServer("sie-lsp", "0.1.0")

    kinds = {"function": types.SymbolKind.Function,
             "method": types.SymbolKind.Method,
             "struct": types.SymbolKind.Struct,
             "interface": types.SymbolKind.Interface,
             "enum": types.SymbolKind.Enum,
             "constant": types.SymbolKind.Constant,
             "variable": types.SymbolKind.Variable,
             "alias": types.SymbolKind.Class}

    include_paths: list[Path] = []
    outlines: dict[str, list[Symbol]] = {}
    pending: dict[str, asyncio.Task] = {}

    def document_path(uri: str) -> Path:
        return Path(to_fs_path(uri)).resolve()

    def line_range(line: int | None, doc) -> types.Range:
        # underline the whole 1-based line; the parser tracks no columns
        at = min((line or 1) - 1, max(len(doc.lines) - 1, 0))
        width = len(doc.lines[at].rstrip("\n")) if doc.lines else 0
        return types.Range(start=types.Position(line=at, character=0),
                           end=types.Position(line=at, character=width))

    def validate(uri: str) -> None:
        doc = server.workspace.get_text_document(uri)
        path = document_path(uri)

        # every open buffer overlays its file, so cross-file edits
        # analyze as typed, saved or not
        overlays = {str(document_path(d.uri)): d.source
                    for d in server.workspace.text_documents.values()}

        report = analyze(path, include_paths, overlays)
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

        server.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics))

    @server.feature(types.INITIALIZE)
    def initialize(params: types.InitializeParams) -> None:
        root = None
        if params.root_uri is not None:
            root = Path(to_fs_path(params.root_uri))

        extra = (params.initialization_options or {}).get("includePaths", [])
        include_paths.extend(search_paths(root, extra))

    @server.feature(types.TEXT_DOCUMENT_DID_OPEN)
    def did_open(params: types.DidOpenTextDocumentParams) -> None:
        validate(params.text_document.uri)

    @server.feature(types.TEXT_DOCUMENT_DID_SAVE)
    def did_save(params: types.DidSaveTextDocumentParams) -> None:
        validate(params.text_document.uri)

    @server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
    async def did_change(params: types.DidChangeTextDocumentParams) -> None:
        # let the keystrokes settle, then recompile; a newer change
        # cancels a wait still in flight
        uri = params.text_document.uri
        if (task := pending.pop(uri, None)) is not None:
            task.cancel()

        async def settled() -> None:
            await asyncio.sleep(0.2)
            validate(uri)

        pending[uri] = asyncio.get_running_loop().create_task(settled())

    @server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
    def did_close(params: types.DidCloseTextDocumentParams) -> None:
        uri = params.text_document.uri
        outlines.pop(uri, None)
        server.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(uri=uri, diagnostics=[]))

    @server.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
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
