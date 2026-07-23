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
from dataclasses import dataclass, field
from pathlib import Path

from siec.ast import Function, Let, Program
from siec.cli import error_parts
from siec.codegen import CodeGenerator, codegen
from siec.codegen.generator import Variable
from siec.codegen.types import is_reference, strip_const, strip_reference
from siec.lexer import Token, lex
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


def compile_unit(path: Path, include_paths: list[Path],
                 overlays: dict[str, str] | None = None) -> Analysis:
    """
    Compile one file as its own unit, keeping whatever the front end
    built: the merged program, the generator, and the first error.

    Overlays stand in for on-disk contents, so unsaved edits analyze
    live; nothing is emitted to native code.
    """
    path = path.resolve()
    paths = [*include_paths, path.parent / "lib"]

    gen = CodeGenerator(str(path))
    program = None
    report = None
    try:
        program = load_program([path], paths, overlays=overlays)
        codegen(program, str(path), define_imports=False, gen=gen)
    except (SyntaxError, TypeError, NameError, FileNotFoundError) as error:
        file, line, message = error_parts(error)
        report = Report(file or str(path), line, message)

    return Analysis(str(path), report, program,
                    gen if program is not None else None, overlays)


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


def token_chain(tokens: list[Token], line: int, col: int):
    """
    The name chain ending at the cursor: the identifier at the 0-based
    position, walked back through its '.' and '::' links. Returns the
    parts, the separators between them, the cursor's token, and the
    syntax following the chain; None off any identifier.

    A chain hanging off a wider expression ('get(i).x') stops at the
    link: the receiver's type is the emitted expression's business.
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

    # a link into a non-name receiver: the chain is not a pure name
    if start >= 1 and tokens[start - 1].syntax in (".", "::"):
        return None

    parts = [tokens[i].value for i in range(start, at + 1, 2)]
    seps = [tokens[i].syntax for i in range(start + 1, at, 2)]
    following = tokens[at + 1].syntax if at + 1 < len(tokens) else None
    return parts, seps, tokens[at], following


def enclosing_function(program: Program, file: str, line: int) -> Function | None:
    """
    The defined function whose body the 1-based line sits in: the last
    one starting at or before it, unless another declaration starts
    between them, which puts the line past the body's end.
    """
    best = None
    for fn in program.functions:
        if fn.file == file and fn.body is not None and fn.line <= line:
            if best is None or fn.line > best.line:
                best = fn

    if best is None:
        return None

    for decl in (*program.functions, *program.structs, *program.enums,
                 *program.consts, *program.globals, *program.aliases):
        if decl.file == file and best.line < decl.line <= line:
            return None

    return best


def local_scope(gen: CodeGenerator, fn: Function, line: int):
    """
    The names in scope at a 1-based line of a function's body, each with
    its declared or inferred type and its declaring line. Source order
    approximates block scope: every 'let' at or above the line counts.
    """
    from dataclasses import fields as dataclass_fields, is_dataclass

    from siec.codegen.inference import infer_type

    scope: dict[str, Variable] = {}
    lines: dict[str, int] = {}

    for param in fn.params:
        scope[param.name] = Variable(None, param.type)
        lines[param.name] = fn.line

    def walk(node) -> None:
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return

        if not is_dataclass(node):
            return

        if isinstance(node, Let) and node.line and node.line <= line:
            type_ = node.type
            if type_ is None and node.value is not None:
                try:
                    type_ = infer_type(gen, node.value, dict(scope))
                except (TypeError, NameError):
                    type_ = None

            if type_ is not None:
                scope[node.name] = Variable(None, type_)

            lines[node.name] = node.line

        for f in dataclass_fields(node):
            walk(getattr(node, f.name))

    walk(fn.body)
    return scope, lines


def signature(fn: Function) -> str:
    """
    A function's declaration in Sie syntax, its generic parameters kept.

    An interface-typed parameter became a synthetic constrained type
    parameter at registration; it renders back as the interface it was
    declared with.
    """
    from siec.codegen.generics import substitute

    mapping = {}
    type_params = list(fn.type_params or ())
    for param, constraint in (fn.constraints or {}).items():
        if param.startswith("__"):
            mapping[param] = constraint
            if param in type_params:
                type_params.remove(param)

    name = fn.name
    if fn.receiver_params and fn.receiver:
        name = (f"{fn.receiver}<{', '.join(fn.receiver_params)}>"
                f"::{fn.name.partition('::')[2]}")

    if type_params:
        name += f"<{', '.join(type_params)}>"

    params = ", ".join(
        p.type if p.name == "self" and is_reference(strip_const(p.type))
        else f"{p.name}: {substitute(p.type, mapping)}"
        for p in fn.params)
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
        name += f"<{', '.join(node.params)}>"

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


def declaration_sites(program: Program):
    """
    Every top-level declaration by name: (kind, node) pairs, the prelude's
    included (their empty file marks them unnavigable).
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

    chain = token_chain(tokens, line, col)
    if chain is None:
        return None

    parts, seps, token, following = chain
    gen = analysis.gen
    gen.current_file = analysis.path

    fn = enclosing_function(analysis.program, analysis.path, token.line)
    scope, lines = local_scope(gen, fn, token.line) if fn else ({}, {})
    sites = declaration_sites(analysis.program)

    try:
        return resolve_chain(analysis, sites, scope, lines, parts, seps,
                             following)
    except (TypeError, NameError, SyntaxError, KeyError, IndexError):
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

    targets = [(decl.file, decl.line) for _, decl in found if decl.file]

    kind = found[0][0]
    if kind == "function":
        texts = []
        for _, decl in found:
            if (sig := signature(decl)) not in texts:
                texts.append(sig)

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
        if node is not None and node.file:
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
    from siec.codegen.inference import expr_sie_type
    from siec.parser.expressions import parse_expression
    from siec.parser.stream import TokenStream

    gen = analysis.gen
    expr = parse_expression(TokenStream(lex(receiver)))
    recv_type = expr_sie_type(gen, expr, scope)
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
    if node is not None and node.file:
        targets = [(node.file, found.line or node.line)]

    return Finding(f"{name}: {found.type}", targets)


def method_finding(analysis: Analysis, sites: dict, base: str,
                   name: str) -> Finding | None:
    """
    A method on a base type: every overload's signature, from the
    concrete declarations and the generic struct's templates alike.
    """
    from siec.codegen.generics import split_generic
    from siec.codegen.methods import resolve_method

    gen = analysis.gen
    try:
        symbol = resolve_method(gen, base, name)
    except (TypeError, NameError):
        symbol = None

    nodes = [decl for kind, decl in sites.get(f"{base}::{name}", ())
             if kind == "function"]
    if (parts := split_generic(base)) is not None:
        nodes.extend(gen.generic_methods.get((parts[0], name), ()))

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

    targets = [(node.file, node.line) for node in nodes if node.file]
    return Finding("\n".join(texts), targets) if texts else None


def create_server():
    """
    Build the pygls server: diagnostics published on open, change (a
    beat after the last keystroke), and save; document symbols from the
    outline; hover and go-to-definition from the last good analysis.
    Initialization options may carry {"includePaths": [...]}.
    """
    import asyncio

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
             "variable": types.SymbolKind.Variable,
             "alias": types.SymbolKind.Class}

    include_paths: list[Path] = []
    outlines: dict[str, list[Symbol]] = {}
    analyses: dict[str, Analysis] = {}
    pending: dict[str, asyncio.Task] = {}

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

    def validate(uri: str) -> None:
        doc = server.workspace.get_text_document(uri)
        path = document_path(uri)

        # every open buffer overlays its file, so cross-file edits
        # analyze as typed, saved or not
        overlays = {str(document_path(d.uri)): d.source
                    for d in server.workspace.text_documents.values()}

        analysis = compile_unit(path, include_paths, overlays)
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
        analyses.pop(uri, None)
        server.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(uri=uri, diagnostics=[]))

    @server.feature(types.TEXT_DOCUMENT_HOVER)
    def hover(params: types.HoverParams) -> types.Hover | None:
        finding = resolve(params.text_document.uri, params.position)
        if finding is None:
            return None

        content = types.MarkupContent(kind=types.MarkupKind.Markdown,
                                      value=f"```sie\n{finding.text}\n```")
        return types.Hover(contents=content)

    @server.feature(types.TEXT_DOCUMENT_DEFINITION)
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
