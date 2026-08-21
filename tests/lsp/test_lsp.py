"""Tests for siec.lsp: the analysis behind the language server."""

import asyncio
import threading
import time
from types import SimpleNamespace

from siec.lsp import (Report, SearchPathCache, UnitAnalysisCache, analyze,
                      call_signature_help, compile_unit, complete,
                      dependent_uris, outline, search_paths)


def write(path, text):
    """
    Create a source file (and its parents) with the given text.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_analyze_clean_source_reports_nothing(tmp_path):
    """
    A file that compiles yields no report.
    """
    src = write(tmp_path / "main.sie", "fn main() -> i32 { return 0; }")
    assert analyze(src, []) is None


def test_analyze_reports_the_error_with_its_line(tmp_path):
    """
    A compile error comes back with the file and its 1-based line.
    """
    src = write(tmp_path / "main.sie",
                'fn main() -> i32 {\n    return "no";\n}')

    report = analyze(src, [])
    assert report == Report(str(src.resolve()), 2,
                            "cannot implicitly convert char[] to i32")


def test_analyze_sanitizes_parser_and_constant_input_failures(tmp_path):
    """Known resource and evaluator failures become ordinary LSP reports."""
    cases = (
        ("fn main() { let value = " + "(" * 1200 + "1"
         + ")" * 1200 + "; }", "source nesting exceeds"),
        ("fn main() { let value = " + "9" * 5000 + "; }",
         "integer literal exceeds 4096 digits"),
        ("enum Broken { VALUE = 1 / 0 } fn main() {}",
         "division by zero in constant expression"),
    )

    for index, (source, message) in enumerate(cases):
        src = write(tmp_path / f"case{index}.sie", source)
        report = analyze(src, [])
        assert report is not None
        assert message in report.message


def test_analyze_preserves_generic_call_trace(tmp_path):
    """
    LSP diagnostics retain the same source-level generic call chain as CLI
    diagnostics.
    """
    src = write(tmp_path / "main.sie", """\
fn fail<T>(value: const &T) -> i32 {
    return value.missing;
}

fn middle<T>(value: const &T) -> i32 {
    return fail(value);
}

fn main() -> i32 {
    let value: i32 = 0;
    return middle(value);
}
""")

    report = analyze(src, [])
    assert report.line == 2
    assert "call trace:" in report.message
    assert "middle<i32>" in report.message
    assert "main" in report.message


def test_analyze_reports_parse_errors(tmp_path):
    """
    Lexer and parser errors locate the same way as codegen's.
    """
    src = write(tmp_path / "main.sie", "fn main() -> i32 { return 0 }")

    report = analyze(src, [])
    assert report.file == str(src.resolve())
    assert report.line == 1
    assert "expected" in report.message


def test_analyze_prefers_overlay_text(tmp_path):
    """
    An overlay stands in for the file on disk: the editor's unsaved
    buffer analyzes, not the stale saved copy.
    """
    src = write(tmp_path / "main.sie", "fn main() -> i32 { broken }")
    fixed = "fn main() -> i32 { return 0; }"

    assert analyze(src, [], {str(src.resolve()): fixed}) is None


def test_compile_unit_collects_deprecation_warnings(tmp_path):
    """
    Deprecation warnings arrive as structured diagnostics, not stderr.
    """
    analysis, _ = unit(tmp_path, """\
@deprecated("use later") fn old() -> i32 { return 1; }
fn main() -> i32 { return old() - 1; }
""")

    assert analysis.report is None
    assert len(analysis.diagnostics) == 1
    warning = analysis.diagnostics[0]
    assert warning.severity == "warning"
    assert warning.code == "deprecated"
    assert "'old' is deprecated: use later" in warning.message


def test_compile_unit_reuses_unchanged_sources_and_complete_analysis(
        tmp_path, monkeypatch):
    """
    An edit reparses only the changed file. Repeating the same inputs then
    returns the complete cached semantic analysis without parsing again.
    """
    import siec.loader as loader

    dependency = write(
        tmp_path / "util.sie",
        "fn answer() -> i32 { return 42; }",
    )
    src = write(
        tmp_path / "main.sie",
        "import util;\nfn main() -> i32 { return 0; }",
    )
    original_parse = loader.parse
    calls = 0

    def counted_parse(tokens):
        nonlocal calls
        calls += 1
        return original_parse(tokens)

    monkeypatch.setattr(loader, "parse", counted_parse)
    cache = UnitAnalysisCache()

    first = compile_unit(src, [], cache=cache)
    assert first.report is None
    assert calls == 2
    assert first.files == frozenset({
        str(src.resolve()),
        str(dependency.resolve()),
    })

    changed_main = {
        str(src.resolve()):
            "import util;\nfn main() -> i32 { return 1; }",
    }
    second = compile_unit(src, [], changed_main, cache)
    assert second.report is None
    assert second is not first
    assert calls == 3

    repeated = compile_unit(src, [], changed_main, cache)
    assert repeated is second
    assert calls == 3

    changed_dependency = {
        **changed_main,
        str(dependency.resolve()):
            "fn answer() -> i32 { return 43; }",
    }
    refreshed = compile_unit(src, [], changed_dependency, cache)
    assert refreshed.report is None
    assert refreshed is not second
    assert calls == 4

    broken_main = {
        **changed_dependency,
        str(src.resolve()):
            'import util;\nfn main() -> i32 { return "wrong"; }',
    }
    broken = compile_unit(src, [], broken_main, cache)
    assert broken.report is not None
    assert calls == 5

    repeated_error = compile_unit(src, [], broken_main, cache)
    assert repeated_error is broken
    assert calls == 5


def test_dependent_uris_follow_the_loaded_source_graph(tmp_path):
    """An imported file edit identifies every open unit that loaded it."""
    dependency = write(tmp_path / "util.sie", "fn answer() -> i32 { return 42; }")
    one = write(
        tmp_path / "one.sie",
        "import util;\nfn one() -> i32 { return 1; }",
    )
    two = write(tmp_path / "two.sie", "fn two() -> i32 { return 2; }")

    loaded_files = {
        "file:///one.sie": compile_unit(one, []).files,
        "file:///two.sie": compile_unit(two, []).files,
    }

    assert dependent_uris(loaded_files, dependency) == ["file:///one.sie"]


def test_compile_cache_tracks_missing_and_shadowing_import_candidates(tmp_path):
    """
    A missing import may be cached, but creating it invalidates the failure.
    Likewise, a new local module invalidates an include-path resolution that
    it now shadows.
    """
    cache = UnitAnalysisCache()
    missing_main = write(
        tmp_path / "missing" / "main.sie",
        "import absent;\nfn main() -> i32 { return 0; }",
    )

    missing = compile_unit(missing_main, [], cache=cache)
    assert missing.report is not None
    assert compile_unit(missing_main, [], cache=cache) is missing

    absent = write(
        missing_main.parent / "absent.sie",
        "fn available() -> i32 { return 1; }",
    )
    recovered = compile_unit(missing_main, [], cache=cache)
    assert recovered.report is None
    assert recovered is not missing
    assert str(absent.resolve()) in recovered.files

    installed = write(
        tmp_path / "include" / "util.sie",
        "fn installed() -> i32 { return 1; }",
    )
    shadow_main = write(
        tmp_path / "shadow" / "main.sie",
        "import util;\nfn main() -> i32 { return 0; }",
    )
    initial = compile_unit(shadow_main, [installed.parent], cache=cache)
    assert initial.report is None
    binding = (str(shadow_main.resolve()), "util")
    assert initial.program.module_bindings[binding] == str(installed.resolve())

    local = write(
        shadow_main.parent / "util.sie",
        "fn local() -> i32 { return 2; }",
    )
    shadowed = compile_unit(shadow_main, [installed.parent], cache=cache)
    assert shadowed.report is None
    assert shadowed is not initial
    assert shadowed.program.module_bindings[binding] == str(local.resolve())


def test_analyze_blames_an_imported_modules_file(tmp_path):
    """
    An error inside an imported module carries that module's file.
    """
    mod = write(tmp_path / "util.sie",
                "fn f() -> i32;\nfn f() -> i64 { return 0; }")
    src = write(tmp_path / "main.sie",
                "import util;\n\nfn main() -> i32 { return 0; }")

    report = analyze(src, [])
    assert report.file == str(mod.resolve())


def test_analyze_checks_the_file_as_its_own_unit(tmp_path):
    """
    Analysis type-checks the edited file's bodies against an imported
    module's declarations without demanding the module's definitions
    emit, the way '-c' compiles.
    """
    write(tmp_path / "util.sie", "fn add(x: i32, y: i32) -> i32 { return x + y; }")
    src = write(tmp_path / "main.sie", """
        import { add } from util;

        fn main() -> i32 { return add(1, null); }
    """)

    report = analyze(src, [])
    assert report.file == str(src.resolve())
    assert report.line == 4


def test_debounced_validation_retires_completed_and_replaced_tasks():
    """
    A completed task leaves no retained entry, and a canceled predecessor
    cannot discard the replacement for the same URI.
    """
    from siec.lsp import _ValidationDebouncer

    async def exercise():
        validated = []
        debounce = _ValidationDebouncer(validated.append, delay=0.01)
        uri = "file:///p.sie"

        debounce.schedule(uri)
        await asyncio.sleep(0)  # let the predecessor enter its wait
        debounce.schedule(uri)
        replacement = debounce.pending[uri]
        await asyncio.sleep(0)

        assert debounce.pending[uri] is replacement

        await asyncio.sleep(0.02)
        assert validated == [uri]
        assert debounce.pending == {}

    asyncio.run(exercise())


def test_debounced_validation_consumes_and_reports_unexpected_failures():
    """A scheduled compiler failure never reaches the loop exception handler."""
    from siec.lsp import _ValidationDebouncer

    async def exercise():
        handled = []
        leaked = []

        async def fail(_uri):
            raise RuntimeError("private compiler detail")

        debounce = _ValidationDebouncer(
            fail, delay=0,
            on_error=lambda uri, error: handled.append((uri, str(error))))
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: leaked.append(context))

        uri = "file:///broken.sie"
        debounce.schedule(uri)
        await debounce.pending[uri]
        await asyncio.sleep(0)

        assert handled == [(uri, "private compiler detail")]
        assert leaked == []
        assert debounce.pending == {}

    asyncio.run(exercise())


def test_debounced_sync_validation_does_not_block_the_event_loop():
    """Legacy synchronous validation callbacks run in a worker thread."""
    from siec.lsp import _ValidationDebouncer

    async def exercise():
        started = threading.Event()
        finished = threading.Event()

        def blocking(_uri):
            started.set()
            time.sleep(0.08)
            finished.set()

        debounce = _ValidationDebouncer(blocking, delay=0)
        debounce.schedule("file:///slow.sie")
        task = debounce.pending["file:///slow.sie"]

        while not started.is_set():
            await asyncio.sleep(0.001)

        before = time.monotonic()
        await asyncio.sleep(0.01)
        assert time.monotonic() - before < 0.05
        assert not finished.is_set()

        await task
        assert finished.is_set()

    asyncio.run(exercise())


def test_running_validation_is_superseded_before_it_can_publish():
    """A completed worker cannot publish after a newer URI generation wins."""
    from siec.lsp import _ValidationDebouncer

    async def exercise():
        first_started = threading.Event()
        release_first = threading.Event()
        invoked = 0
        published = []

        async def validate(_uri):
            nonlocal invoked
            invoked += 1
            generation = invoked
            if generation == 1:
                first_started.set()
                await asyncio.to_thread(release_first.wait)
            published.append(generation)

        debounce = _ValidationDebouncer(validate, delay=0)
        uri = "file:///changing.sie"
        debounce.schedule(uri)
        while not first_started.is_set():
            await asyncio.sleep(0.001)

        debounce.schedule(uri)
        replacement = debounce.pending[uri]
        await replacement
        release_first.set()
        await asyncio.sleep(0.01)

        assert published == [2]
        assert debounce.pending == {}

    asyncio.run(exercise())


def test_shutdown_cancels_pending_validation(tmp_path, monkeypatch):
    """The LSP shutdown request retires delayed compiler work immediately."""
    from lsprotocol import types
    from pygls.uris import from_fs_path

    from siec.lsp import create_server

    uri = from_fs_path(str(tmp_path / "main.sie"))

    async def exercise():
        server = create_server()
        published = []
        monkeypatch.setattr(server, "text_document_publish_diagnostics",
                            published.append)
        did_change = server.protocol.fm.features[types.TEXT_DOCUMENT_DID_CHANGE]
        shutdown = server.protocol.fm.features[types.SHUTDOWN]

        await did_change(types.DidChangeTextDocumentParams(
            text_document=types.VersionedTextDocumentIdentifier(
                uri=uri, version=1),
            content_changes=[]))
        shutdown()
        await asyncio.sleep(0.25)

        assert published == []

    asyncio.run(exercise())


def test_close_cancels_a_pending_validation(tmp_path, monkeypatch):
    """
    Closing during the debounce window publishes only the clearing
    diagnostics and never lets delayed validation touch the closed URI.
    """
    from lsprotocol import types
    from pygls.uris import from_fs_path

    from siec.lsp import create_server

    src = write(tmp_path / "main.sie", "fn main() -> i32 { broken }")
    uri = from_fs_path(str(src))

    async def exercise():
        server = create_server()
        published = []
        errors = []
        monkeypatch.setattr(server, "text_document_publish_diagnostics",
                            published.append)
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: errors.append(context))

        did_change = server.protocol.fm.features[types.TEXT_DOCUMENT_DID_CHANGE]
        did_close = server.protocol.fm.features[types.TEXT_DOCUMENT_DID_CLOSE]
        shutdown = server.protocol.fm.features[types.SHUTDOWN]

        await did_change(types.DidChangeTextDocumentParams(
            text_document=types.VersionedTextDocumentIdentifier(uri=uri, version=1),
            content_changes=[]))
        did_close(types.DidCloseTextDocumentParams(
            text_document=types.TextDocumentIdentifier(uri=uri)))
        await asyncio.sleep(0.25)

        assert [params.diagnostics for params in published] == [[]]
        assert errors == []
        shutdown()

    asyncio.run(exercise())


def test_initialization_rejects_malformed_option_shapes_without_raising(
        monkeypatch):
    """Untrusted initialization JSON is ignored with sanitized warnings."""
    from lsprotocol import types

    from siec.lsp import create_server

    server = create_server()
    logs = []
    monkeypatch.setattr(server, "window_log_message", logs.append)
    initialize = server.protocol.fm.features[types.INITIALIZE]
    shutdown = server.protocol.fm.features[types.SHUTDOWN]

    initialize(SimpleNamespace(root_uri=None, initialization_options=[]))
    initialize(SimpleNamespace(
        root_uri=None,
        initialization_options={"includePaths": "one/path", "debug": "yes"}))

    assert len(logs) == 3
    assert all(log.type == types.MessageType.Warning for log in logs)
    assert all("Traceback" not in log.message for log in logs)
    shutdown()


def test_failed_server_validation_clears_stale_analysis_and_is_sanitized(
        tmp_path, monkeypatch):
    """An internal compiler bug replaces old semantics without leaking detail."""
    from lsprotocol import types
    from pygls.uris import from_fs_path
    from pygls.workspace import Workspace

    import siec.lsp as lsp

    src = write(tmp_path / "main.sie", "fn main() -> i32 { return 0; }")
    uri = from_fs_path(str(src))

    async def exercise():
        server = lsp.create_server()
        server.protocol._workspace = Workspace(None)
        server.workspace.put_text_document(types.TextDocumentItem(
            uri=uri, language_id="sie", version=1, text=src.read_text()))

        published = []
        logs = []
        leaked = []
        monkeypatch.setattr(server, "text_document_publish_diagnostics",
                            published.append)
        monkeypatch.setattr(server, "window_log_message", logs.append)
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: leaked.append(context))

        did_open = server.protocol.fm.features[types.TEXT_DOCUMENT_DID_OPEN]
        did_save = server.protocol.fm.features[types.TEXT_DOCUMENT_DID_SAVE]
        hover = server.protocol.fm.features[types.TEXT_DOCUMENT_HOVER]
        shutdown = server.protocol.fm.features[types.SHUTDOWN]

        await did_open(types.DidOpenTextDocumentParams(
            text_document=types.TextDocumentItem(
                uri=uri, language_id="sie", version=1,
                text=src.read_text())))
        params = types.HoverParams(
            text_document=types.TextDocumentIdentifier(uri=uri),
            position=types.Position(line=0, character=4))
        assert hover(params) is not None

        def fail_compile(*_args, **_kwargs):
            raise RuntimeError("private compiler detail")

        monkeypatch.setattr(lsp, "compile_unit", fail_compile)
        await did_save(types.DidSaveTextDocumentParams(
            text_document=types.TextDocumentIdentifier(uri=uri)))
        await asyncio.sleep(0)

        assert hover(params) is None
        assert leaked == []
        assert len(published[-1].diagnostics) == 1
        message = published[-1].diagnostics[0].message
        assert "failed unexpectedly" in message
        assert "private compiler detail" not in message
        assert logs
        assert all("private compiler detail" not in log.message for log in logs)
        assert all("Traceback" not in log.message for log in logs)
        shutdown()

    asyncio.run(exercise())


def test_request_failure_returns_a_safe_fallback_without_traceback(
        tmp_path, monkeypatch):
    """Unexpected hover failures are logged and answered as no result."""
    from lsprotocol import types
    from pygls.uris import from_fs_path
    from pygls.workspace import Workspace

    import siec.lsp as lsp

    src = write(tmp_path / "main.sie", "fn main() -> i32 { return 0; }")
    uri = from_fs_path(str(src))

    async def exercise():
        server = lsp.create_server()
        server.protocol._workspace = Workspace(None)
        server.workspace.put_text_document(types.TextDocumentItem(
            uri=uri, language_id="sie", version=1, text=src.read_text()))
        logs = []
        monkeypatch.setattr(server, "text_document_publish_diagnostics",
                            lambda _params: None)
        monkeypatch.setattr(server, "window_log_message", logs.append)

        did_open = server.protocol.fm.features[types.TEXT_DOCUMENT_DID_OPEN]
        hover = server.protocol.fm.features[types.TEXT_DOCUMENT_HOVER]
        shutdown = server.protocol.fm.features[types.SHUTDOWN]
        await did_open(types.DidOpenTextDocumentParams(
            text_document=types.TextDocumentItem(
                uri=uri, language_id="sie", version=1,
                text=src.read_text())))

        def fail_inspection(*_args, **_kwargs):
            raise RuntimeError("private request detail")

        monkeypatch.setattr(lsp, "inspect", fail_inspection)
        result = hover(types.HoverParams(
            text_document=types.TextDocumentIdentifier(uri=uri),
            position=types.Position(line=0, character=4)))

        assert result is None
        assert logs[-1].type == types.MessageType.Error
        assert "private request detail" not in logs[-1].message
        assert "Traceback" not in logs[-1].message
        shutdown()

    asyncio.run(exercise())


def test_outline_lists_declarations_in_source_order(tmp_path):
    """
    Every top-level declaration appears with its kind and line.
    """
    symbols = outline("""\
@const LIMIT = 8;

struct Point { x: i32; y: i32; }

interface Shape;

enum Color { RED, BLUE }

@type Pair = Point;

@static let count: i32 = 0;

fn area(p: Point) -> i32 { return p.x * p.y; }

fn Point::flip(&self) { }
""")

    assert [(s.name, s.kind, s.line) for s in symbols] == [
        ("LIMIT", "constant", 1),
        ("Point", "struct", 3),
        ("Shape", "interface", 5),
        ("Color", "enum", 7),
        ("Pair", "alias", 9),
        ("count", "variable", 11),
        ("area", "function", 13),
        ("Point::flip", "method", 15),
    ]


def test_outline_returns_none_when_the_text_does_not_parse():
    """
    Broken text yields None so the caller can keep the last good outline.
    """
    assert outline("fn broken( {") is None


def test_search_paths_read_the_project_config(tmp_path):
    """
    'package.toml' configures the include path: the workspace root's
    '[package] include' entries join after any explicit ones, with the
    root and its 'lib/' closing.
    """
    write(tmp_path / "package.toml",
          '[package]\ninclude = ["packages/core/src", "packages/libc/src"]\n')
    (tmp_path / "packages" / "core" / "src").mkdir(parents=True)

    paths = search_paths(tmp_path, ["/explicit"])
    assert [str(p) for p in paths] == [
        "/explicit",
        str((tmp_path / "packages" / "core" / "src").resolve()),
        str((tmp_path / "packages" / "libc" / "src").resolve()),
        str(tmp_path),
        str(tmp_path / "lib"),
    ]


def test_search_paths_cache_manifest_resolution_until_an_input_changes(
        tmp_path, monkeypatch):
    """
    Repeated validation does not reread a stable manifest; rewriting it
    invalidates the path result without restarting the server.
    """
    import siec.lsp as lsp

    manifest = write(
        tmp_path / "package.toml",
        '[package]\ninclude = ["first"]\n',
    )
    original = lsp.config_paths
    calls = 0

    def counted_config_paths(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(lsp, "config_paths", counted_config_paths)
    cache = SearchPathCache()

    first = search_paths(tmp_path, [], tmp_path, cache)
    repeated = search_paths(tmp_path, [], tmp_path, cache)
    assert repeated == first
    assert calls == 1

    manifest.write_text('[package]\ninclude = ["second-longer"]\n')
    changed = search_paths(tmp_path, [], tmp_path, cache)
    assert calls == 2
    assert tmp_path / "second-longer" in changed


def test_search_paths_prefer_the_nearest_config(tmp_path):
    """
    The nearest 'package.toml' walking up from the edited file
    contributes first, the workspace root's after.
    """
    write(tmp_path / "package.toml", '[package]\ninclude = ["packages"]\n')
    write(tmp_path / "sie" / "package.toml", '[package]\ninclude = ["src"]\n')

    paths = search_paths(tmp_path, [], tmp_path / "sie" / "src")
    assert [str(p) for p in paths] == [
        str((tmp_path / "sie" / "src").resolve()),
        str((tmp_path / "packages").resolve()),
        str(tmp_path),
        str(tmp_path / "lib"),
    ]


def test_search_paths_follow_a_package_to_its_dependencies(tmp_path, monkeypatch):
    """
    A manifest declaring a package of its own puts that package's
    sources on the include path, and its installed dependencies' after,
    the way a build assembles them.
    """
    monkeypatch.setenv("SIE_PATH", str(tmp_path / "sie"))

    installed = tmp_path / "sie" / "lib" / "core@1.0.0"
    write(installed / "package.toml",
          '[package]\nname = "core"\nversion = "1.0.0"\n\n'
          '[library]\nsources = ["src/"]\n')
    (installed / "src").mkdir(parents=True)

    project = tmp_path / "app"
    write(project / "package.toml",
          '[package]\nname = "app"\n\n[app]\nsources = ["src/"]\n\n'
          '[dependencies]\ncore = "*"\n')
    (project / "src").mkdir(parents=True)

    paths = search_paths(project, [], project / "src")
    assert [str(p) for p in paths] == [
        str(project / "src"),
        str(installed / "src"),
        str(project),
        str(project / "lib"),
    ]


def test_search_paths_prefer_a_configured_directory_to_a_resolved_one(tmp_path,
                                                                     monkeypatch):
    """
    An 'include' entry is a deliberate override, so it comes first
    whichever manifest named it: a checkout pointing at its own packages
    wins over the copies a dependency resolves to.
    """
    monkeypatch.setenv("SIE_PATH", str(tmp_path / "sie"))

    installed = tmp_path / "sie" / "lib" / "core@1.0.0"
    write(installed / "package.toml",
          '[package]\nname = "core"\nversion = "1.0.0"\n\n'
          '[library]\nsources = ["src/"]\n')
    (installed / "src").mkdir(parents=True)

    write(tmp_path / "package.toml", '[package]\ninclude = ["packages/core/src"]\n')
    (tmp_path / "packages" / "core" / "src").mkdir(parents=True)

    write(tmp_path / "app" / "package.toml",
          '[package]\nname = "app"\n\n[app]\nsources = ["src/"]\n\n'
          '[dependencies]\ncore = "*"\n')
    (tmp_path / "app" / "src").mkdir(parents=True)

    paths = search_paths(tmp_path, [], tmp_path / "app" / "src")
    assert [str(p) for p in paths[:3]] == [
        str((tmp_path / "packages" / "core" / "src").resolve()),
        str(tmp_path / "app" / "src"),
        str(installed / "src"),
    ]


def test_search_paths_survive_a_dependency_that_is_not_installed(tmp_path,
                                                                 monkeypatch):
    """
    What cannot be resolved contributes nothing: the package's own
    sources still analyze, and the unresolved import reports itself.
    """
    monkeypatch.setenv("SIE_PATH", str(tmp_path / "sie"))

    project = tmp_path / "app"
    write(project / "package.toml",
          '[package]\nname = "app"\n\n[app]\nsources = ["src/"]\n\n'
          '[dependencies]\nmissing = "*"\n')
    (project / "src").mkdir(parents=True)

    paths = search_paths(project, [], project / "src")
    assert str(project / "src") in [str(p) for p in paths]


def unit(tmp_path, text, name="main.sie"):
    """
    Compile a source file as a unit for inspection.
    """
    from siec.lsp import compile_unit

    src = write(tmp_path / name, text)
    return compile_unit(src, []), src


def probe(analysis, src, line, col):
    """
    Inspect the written file at a 0-based position.
    """
    from siec.lsp import inspect

    return inspect(analysis, src.read_text(), line, col)


def test_inspect_types_a_local_variable(tmp_path):
    """
    Hovering a local shows its inferred type and sites its 'let'.
    """
    analysis, src = unit(tmp_path, """\
fn main() -> i32 {
    let count = 41;
    return count + 1;
}
""")

    finding = probe(analysis, src, 2, 11)
    assert finding.text == "count: i32"
    assert finding.targets == [(str(src.resolve()), 2)]


def test_inspect_does_not_leak_a_local_past_its_block(tmp_path):
    """
    A declaration inside an if is absent after its closing brace, even
    though its source line precedes the cursor.
    """
    analysis, src = unit(tmp_path, """\
fn main() -> i32 {
    if (true) {
        let hidden: i32 = 42;
    }
    return hidden;
}
""")

    assert analysis.report is not None
    assert "undefined variable 'hidden'" in analysis.report.message
    assert probe(analysis, src, 4, 11) is None


def test_inspect_uses_only_the_cursor_lexical_ancestor_chain(tmp_path):
    """
    An inner declaration shadows its outer sibling only inside that block;
    the outer declaration is restored afterward.
    """
    analysis, src = unit(tmp_path, """\
fn main() -> i32 {
    let value: i32 = 1;
    if (true) {
        let value: u64 = 2;
        let inside = value;
    }
    return value;
}
""")

    inside = probe(analysis, src, 4, 21)
    assert inside.text == "value: u64"
    assert inside.targets == [(str(src.resolve()), 4)]

    outside = probe(analysis, src, 6, 11)
    assert outside.text == "value: i32"
    assert outside.targets == [(str(src.resolve()), 2)]


def test_analysis_and_hover_support_index_operator_interfaces(tmp_path):
    """
    Editor analysis accepts indexed structs, infers get_item's result,
    and exposes the two builtin interface declarations to hover.
    """
    analysis, src = unit(tmp_path, """\
struct Table: GetItem<u64, i32>, SetItem<u64, i32> { value: i32; }
fn Table::get_item(const &self, key: u64) -> i32 { return self.value; }
fn Table::set_item(&self, key: u64, value: i32) { self.value = value; }

fn main() -> i32 {
    let table: Table = { 40 };
    table[0] = 42;
    let found = table[0];
    return found;
}
""")

    assert analysis.report is None

    get_item = probe(analysis, src, 0, 16)
    assert get_item.text == "interface GetItem<K, V>;"
    assert get_item.targets == []

    set_item = probe(analysis, src, 0, 35)
    assert set_item.text == "interface SetItem<K, V>;"
    assert set_item.targets == []

    found = probe(analysis, src, 8, 11)
    assert found.text == "found: i32"
    assert found.targets == [(str(src.resolve()), 8)]


def test_inactive_conditional_arms_are_comment_semantic_tokens(tmp_path):
    """
    The compiler's rejected @if arms become line-split 'comment' tokens,
    while the selected arm and the directives themselves stay active.
    """
    from siec.lsp import inactive_semantic_tokens

    analysis, src = unit(tmp_path, """\
@if (false) {
    @const OFF = 1;
} @else {
    @const ON = 1;
}

fn main() -> i32 { return ON; }
""")

    text = src.read_text()
    encoded = inactive_semantic_tokens(analysis, text)
    assert encoded == [1, 0, len("    @const OFF = 1;"), 0, 0]


def test_inactive_conditional_tokens_follow_else_if_chains(tmp_path):
    """
    A selected nested @else @if dims the earlier body and the final else,
    without dimming the selected middle body.
    """
    from siec.lsp import inactive_semantic_tokens

    analysis, src = unit(tmp_path, """\
@const PICK = 2;
@if (PICK == 1) {
    @const FIRST = 1;
} @else @if (PICK == 2) {
    @const CHOSEN = 2;
} @else {
    @const LAST = 3;
}

fn main() -> i32 { return CHOSEN - 2; }
""")

    encoded = inactive_semantic_tokens(analysis, src.read_text())

    # Decode only the line numbers; one token is emitted per visible line
    # of inactive source.
    lines = []
    line = 0
    for offset in range(0, len(encoded), 5):
        line += encoded[offset]
        lines.append(line)

    assert lines == [2, 6]


def test_server_advertises_comment_semantic_tokens():
    """
    Every LSP client sees a full-document semantic-token provider whose
    only token type is the standard, theme-portable 'comment'.
    """
    from lsprotocol import types

    from siec.lsp import create_server

    server = create_server()
    options = server.protocol.fm.feature_options[
        types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL]
    assert options == types.SemanticTokensLegend(
        token_types=["comment"], token_modifiers=[])


def test_inspect_shows_a_functions_overloads(tmp_path):
    """
    Hovering a function name lists every overload's signature and
    targets each declaration.
    """
    analysis, src = unit(tmp_path, """\
fn pick(n: i64) -> i64 { return n; }
fn pick(f: f64) -> f64 { return f; }

fn main() -> i32 {
    return pick(2) as i32;
}
""")

    finding = probe(analysis, src, 4, 11)
    assert finding.text == ("fn pick(n: i64) -> i64\n"
                            "fn pick(f: f64) -> f64")
    assert finding.targets == [(str(src.resolve()), 1), (str(src.resolve()), 2)]


def test_inspect_shows_destructured_tuple_parameters(tmp_path):
    """
    Hover keeps the source pattern for a tuple parameter, not the
    synthetic '#N' spill name, and types the bound names in the body.
    """
    analysis, src = unit(tmp_path, """\
fn split((a, b): Tuple<i32, u32>) -> i32 {
    return a + b as i32;
}

fn main() -> i32 {
    return split((1, 2 as u32));
}
""")

    finding = probe(analysis, src, 5, 11)
    assert finding.text == (
        "fn split((a, b): Tuple<i32,u32>) -> i32")

    bound = probe(analysis, src, 1, 11)
    assert bound.text == "a: i32"


def test_inspect_resolves_a_method_through_its_receiver(tmp_path):
    """
    Hovering a method call resolves the receiver's inferred type and
    shows the generic template's signature, sited at the template.
    """
    analysis, src = unit(tmp_path, """\
struct Box<T> { value: T; }

fn Box<T>::get(&self) -> T { return self.value; }

fn main() -> i32 {
    let b: Box<i32>;
    return b.get();
}
""")

    finding = probe(analysis, src, 6, 13)
    assert finding.text == "fn Box<T>::get(&Box<T>) -> T"
    assert finding.targets == [(str(src.resolve()), 3)]


def test_inspect_resolves_a_method_nested_in_its_struct(tmp_path):
    """Nested methods join the same hover and definition index."""
    analysis, src = unit(tmp_path, """\
struct Box<T> {
    value: T;
    fn get(const &self) -> T { return self.value; }
}

fn main() -> i32 {
    let box: Box<i32> = { 42 };
    return box.get();
}
""")

    finding = probe(analysis, src, 7, 15)
    assert finding.text == "fn Box<T>::get(const &Box<T>) -> T"
    assert finding.targets == [(str(src.resolve()), 3)]


def test_method_hover_does_not_instantiate_after_compilation(tmp_path):
    """Hover reads an unused generic method without reopening compilation."""
    from siec.lsp import declaration_sites, method_finding

    analysis, src = unit(tmp_path, """\
struct Box<T> { value: T; }

fn Box<T>::get(const &self) -> const &T { return self.value; }

fn main() -> i32 {
    let box: Box<i32>;
    return 0;
}
""")

    assert analysis.report is None
    assert "Box<i32>::get" not in analysis.gen.instantiated_functions

    finding = method_finding(
        analysis,
        declaration_sites(analysis.program),
        "Box<i32>",
        "get",
    )

    assert finding.text == "fn Box<T>::get(const &Box<T>) -> const &T"
    assert finding.targets == [(str(src.resolve()), 3)]
    assert "Box<i32>::get" not in analysis.gen.instantiated_functions


def test_method_hover_hides_colliding_module_type_identity(tmp_path):
    """Compiler-private module identities never appear in method hover."""
    write(tmp_path / "adw.sie", """\
struct HeaderBar {
    pad: i32;
    fn init(&self) { self.pad = 0; }
    fn pack_end(&self, child: i32) {}
}
""")
    write(tmp_path / "gtk.sie", "struct HeaderBar { pad: i32; }\n")

    analysis, src = unit(tmp_path, """\
import adw;
import gtk;

fn main() {
    let bar = adw.HeaderBar();
    bar.pack_end(1);
}
""")

    finding = probe(analysis, src, 5, 10)
    assert finding.text == "fn HeaderBar::pack_end(&HeaderBar, child: i32)"


def test_hover_in_generic_body_does_not_reopen_instantiation(tmp_path):
    """
    Hovering inside an unstamped generic method must not crash when a
    local's initializer would stamp a still-free type after lowering.
    """
    analysis, src = unit(tmp_path, """\
struct Node<T> { next: Node<T>*; }

fn Node<T>::push(&self) {
    let node = null as Node<T>*;
    self.next = node;
}

fn main() -> i32 {
    return 0;
}
""")

    assert analysis.report is None
    finding = probe(analysis, src, 4, 16)
    assert finding is not None
    assert "node" in finding.text
    assert finding.targets == [(str(src.resolve()), 4)]


def test_inspect_renders_intersection_bounds(tmp_path):
    """Hover preserves explicit intersections in generic signatures."""
    analysis, src = unit(tmp_path, """\
interface I1;
interface I2;
struct Both: I1, I2 {}

fn choose<T: I2 & I1>(value: T) -> i32 { return 42; }

fn main() -> i32 {
    let value: Both = {};
    return choose(value);
}
""")

    finding = probe(analysis, src, 8, 11)
    assert finding.text == "fn choose<T: I1 & I2>(value: T) -> i32"


def test_inspect_sites_a_selected_function_override(tmp_path):
    """Hover and navigation omit the concrete implementation it replaces."""
    analysis, src = unit(tmp_path, """\
fn answer() -> i32 { return 1; }

@override
fn answer() -> i32 { return 42; }

fn main() -> i32 {
    return answer();
}
""")

    finding = probe(analysis, src, 6, 12)
    assert finding.text == "fn answer() -> i32"
    assert finding.targets == [(str(src.resolve()), 3)]


def test_inspect_preserves_bounds_in_declarations(tmp_path):
    """
    Hover renders declared bounds on structs and generic methods instead
    of exposing the compiler's internal constraint representation.
    """
    analysis, src = unit(tmp_path, """\
interface Hashable;
struct Key: Hashable { value: i32; }
struct Box<T: Hashable> { value: T; }

fn Box<T>::take<U: Hashable>(&self, value: U) -> T {
    return self.value;
}

fn main() -> i32 {
    let box: Box<Key>;
    let key: Key;
    return box.take(key).value;
}
""")

    struct = probe(analysis, src, 2, 8)
    method = probe(analysis, src, 11, 16)

    assert struct.text == "struct Box<T: Hashable> {\n    value: T;\n}"
    assert method.text == "fn Box<T>::take<U: Hashable>(&Box<T>, value: U) -> T"


def test_inspect_resolves_a_bounded_extension_method(tmp_path):
    """
    A method declared inside a bounded extension block is visible through
    a matching concrete receiver and sites back to the block method.
    """
    analysis, src = unit(tmp_path, """\
interface Hashable {
    fn hash(const &self) -> u64;
}

@extend<T: Scalar> T[]: Hashable {
    fn hash(const &self) -> u64 { return self.length; }
}

fn main() -> i32 {
    let values: i32[] = [1, 2];
    return values.hash() as i32;
}
""")

    finding = probe(analysis, src, 10, 19)
    assert finding.text == "fn T[]::hash(const &T[]) -> u64"
    assert finding.targets == [(str(src.resolve()), 6)]


def test_inspect_sites_the_selected_method_override(tmp_path):
    """Hover follows a bounded override where it applies and the base elsewhere."""
    analysis, src = unit(tmp_path, """\
interface Special;
@extend char: Special;

fn T[]::answer(const &self) -> i32 { return 1; }

@where<T: Special>
@override
fn T[]::answer(const &self) -> i32 { return 42; }

fn main() -> i32 {
    let chars: char[] = "x";
    let ints: i32[] = [1];
    return chars.answer() + ints.answer();
}
""")

    overridden = probe(analysis, src, 12, 18)
    fallback = probe(analysis, src, 12, 34)

    assert overridden.text == "fn T[]::answer(const &T[]) -> i32"
    assert overridden.targets == [(str(src.resolve()), 7)]
    assert fallback.text == "fn T[]::answer(const &T[]) -> i32"
    assert fallback.targets == [(str(src.resolve()), 4)]


def test_inspect_formats_a_bare_bounded_receiver(tmp_path):
    """A bare receiver family renders as T, never as a generic T<T>."""
    analysis, src = unit(tmp_path, """\
interface Hashable {
    fn hash(const &self) -> u64;
}

@extend<T: Scalar> T: Hashable {
    fn hash(const &self) -> u64 { return self as u64; }
}

fn main() -> i32 {
    let value: u8 = 42;
    return value.hash() as i32;
}
""")

    finding = probe(analysis, src, 10, 18)
    assert finding.text == "fn T::hash(const &T) -> u64"
    assert finding.targets == [(str(src.resolve()), 6)]


def test_inspect_resolves_a_template_block_method(tmp_path):
    """A template environment preserves its method's source definition."""
    analysis, src = unit(tmp_path, """\
interface Hashable {
    fn hash(const &self) -> u64;
}

@where<T: Scalar> {
    @extend T[]: Hashable {
        fn hash(const &self) -> u64 { return self.length; }
    }
}

fn main() -> i32 {
    let values: i32[] = [1, 2];
    return values.hash() as i32;
}
""")

    finding = probe(analysis, src, 12, 19)
    assert finding.text == "fn T[]::hash(const &T[]) -> u64"
    assert finding.targets == [(str(src.resolve()), 7)]


def test_inspect_resolves_a_bounded_array_method_through_generic_chain(
        tmp_path):
    """
    A generic method's unresolved T still finds a bounded T[] method
    through another method's array return.
    """
    analysis, src = unit(tmp_path, """\
interface Formattable {
    fn format(const &self, modifiers: const &char[]) -> i32;
}

@where<T: Formattable>
@extend T[]: Formattable {
    fn format(const &self, modifiers: const &char[]) -> i32 {
        return self.length as i32;
    }
}

struct List<T> {
    data: T*;
    length: u64;
}

fn List<T>::as_const_array(const &self) -> const T[] {
    return {self.data, self.length};
}

fn List<T>::format(const &self, modifiers: const &char[]) -> i32 {
    return self.as_const_array().format(modifiers);
}
""")

    finding = probe(analysis, src, 21, 34)
    assert finding.text == (
        "fn T[]::format(const &T[], modifiers: const &char[]) -> i32")
    assert finding.targets == [(str(src.resolve()), 7)]


def test_inspect_types_a_field_through_the_chain(tmp_path):
    """
    Hovering a field types it through the receiver chain and sites its
    line in the struct's declaration.
    """
    analysis, src = unit(tmp_path, """\
struct Point {
    x: i32;
    y: i32;
}

fn main() -> i32 {
    let p: Point;
    return p.y;
}
""")

    finding = probe(analysis, src, 7, 13)
    assert finding.text == "y: i32"
    assert finding.targets == [(str(src.resolve()), 3)]


def test_inspect_resolves_an_enum_member(tmp_path):
    """
    Hovering 'E::M' shows the member's value and sites its line.
    """
    analysis, src = unit(tmp_path, """\
enum Color {
    RED,
    BLUE = 7,
}

fn main() -> i32 {
    return Color::BLUE as i32;
}
""")

    finding = probe(analysis, src, 6, 18)
    assert finding.text == "Color::BLUE = 7"
    assert finding.targets == [(str(src.resolve()), 3)]


def test_inspect_resolves_a_module_member(tmp_path):
    """
    Hovering a qualified module member resolves through the binding to
    the module's declaration.
    """
    write(tmp_path / "util.sie", """\
fn add(x: i32, y: i32) -> i32 { return x + y; }
""")

    analysis, src = unit(tmp_path, """\
import util;

fn main() -> i32 {
    return util.add(40, 2);
}
""")

    finding = probe(analysis, src, 3, 16)
    assert finding.text == "fn add(x: i32, y: i32) -> i32"
    assert finding.targets == [(str((tmp_path / "util.sie").resolve()), 1)]


def test_complete_lists_an_imported_modules_public_members(tmp_path):
    """A trailing module dot offers its exported declarations by kind."""
    write(tmp_path / "util.sie", """\
fn add(x: i32, y: i32) -> i32 { return x + y; }
@const ANSWER: i32 = 42;
struct Box { value: i32; }
@private fn hidden() -> i32 { return 0; }
""")

    analysis, _ = unit(tmp_path, """\
import util;

fn main() -> i32 {
    return util.add(40, 2);
}
""")
    edited = """\
import util;

fn main() -> i32 {
    util.
    return 0;
}
"""

    items = {item.label: item for item in complete(analysis, edited, 3, 9)}
    assert set(items) == {"ANSWER", "Box", "add"}
    assert items["ANSWER"].kind == "constant"
    assert items["Box"].kind == "struct"
    assert items["add"].kind == "function"
    assert items["add"].detail == "fn add(x: i32, y: i32) -> i32"


def test_complete_filters_a_module_member_prefix(tmp_path):
    """The unfinished name after a module dot narrows its exports."""
    write(tmp_path / "util.sie", """\
fn add(x: i32, y: i32) -> i32 { return x + y; }
fn subtract(x: i32, y: i32) -> i32 { return x - y; }
""")
    analysis, _ = unit(tmp_path, """\
import util;
fn main() -> i32 { return util.add(40, 2); }
""")
    edited = """\
import util;
fn main() -> i32 { util.ad }
"""

    items = complete(analysis, edited, 1, 26)
    assert [item.label for item in items] == ["add"]


def test_complete_lists_public_members_inside_a_member_import(tmp_path):
    """An import list completes the named module's public export surface."""
    write(tmp_path / "math" / "util.sie", """\
fn add(x: i32, y: i32) -> i32 { return x + y; }
@const ANSWER: i32 = 42;
struct Box { value: i32; }
@private fn hidden() -> i32 { return 0; }
""")
    source = "import {} from math.util;\nfn main() -> i32 { return 0; }\n"
    analysis, _ = unit(tmp_path, source)

    items = {
        item.label: item
        for item in complete(analysis, source, 0, len("import {"))
    }

    assert set(items) == {"ANSWER", "Box", "add"}
    assert items["ANSWER"].kind == "constant"
    assert items["Box"].kind == "struct"
    assert items["add"].kind == "function"
    assert items["add"].detail == "fn add(x: i32, y: i32) -> i32"


def test_complete_filters_and_deduplicates_multiline_member_imports(tmp_path):
    """A partial import member is filtered and chosen names are not repeated."""
    write(tmp_path / "util.sie", """\
fn add(x: i32, y: i32) -> i32 { return x + y; }
struct Box { value: i32; }
""")
    source = "import {} from util;\nfn main() -> i32 { return 0; }\n"
    analysis, _ = unit(tmp_path, source)
    edited = """\
import {
    add,
    Bo
} from util;
fn main() -> i32 { return 0; }
"""

    items = complete(analysis, edited, 2, len("    Bo"))
    assert [item.label for item in items] == ["Box"]

    after_comma = "import { add,  } from util;"
    items = complete(analysis, after_comma, 0, len("import { add,  "))
    assert [item.label for item in items] == ["Box"]


def test_server_triggers_completion_for_member_import_lists():
    """Opening an import list and adding a member ask for fresh suggestions."""
    from lsprotocol import types

    from siec.lsp import create_server

    server = create_server()
    options = server.protocol.fm.feature_options[
        types.TEXT_DOCUMENT_COMPLETION]
    assert options == types.CompletionOptions(
        trigger_characters=[".", ":", ">", "{", ","])


def signature_at(analysis, text: str):
    """Request signature help at the single '|' cursor marker."""
    offset = text.index("|")
    before = text[:offset]
    source = text[:offset] + text[offset + 1:]
    line = before.count("\n")
    col = len(before.rsplit("\n", 1)[-1])
    return call_signature_help(analysis, source, line, col)


def test_signature_help_for_functions_methods_and_macros(tmp_path):
    """Call help displays parameters for every callable declaration form."""
    source = """\
fn add(x: i32, y: i32) -> i32 { return x + y; }

struct Point {
    fn contains(&self, x: f64, y: f64) -> bool { return true; }
}

@macro choose<T>(left, right) = left as T;

fn main() -> i32 {
    let point: Point = {};
    add(1, 2);
    point.contains(1.0, 2.0);
    return choose<i32>(1, 2);
}
"""
    analysis, _ = unit(tmp_path, source)

    function = signature_at(analysis, source.replace("add(1, 2)",
                                                      "add(1, |)"))
    assert function.active_parameter == 1
    assert function.signatures[0].label == \
        "fn add(x: i32, y: i32) -> i32"
    assert function.signatures[0].parameters == ("x: i32", "y: i32")

    method = signature_at(
        analysis,
        source.replace("point.contains(1.0, 2.0)",
                       "point.contains(1.0, |)"),
    )
    assert method.active_parameter == 1
    assert method.signatures[0].label == \
        "fn Point::contains(x: f64, y: f64) -> bool"
    assert method.signatures[0].parameters == ("x: f64", "y: f64")

    macro = signature_at(
        analysis,
        source.replace("choose<i32>(1, 2)", "choose<i32>(1, |)"),
    )
    assert macro.active_parameter == 1
    assert macro.signatures[0].label == \
        "@macro choose<T>(left, right)"
    assert macro.signatures[0].parameters == ("left", "right")


def test_signature_help_crosses_nested_generic_arguments(tmp_path):
    """A nested generic type list before '(' still leads back to the macro."""
    source = """\
struct Box<T> { value: T; }
@macro make<T>(value) = value;
fn main() -> i32 { make<Box<Box<i32>>>(0); return 0; }
"""
    analysis, _ = unit(tmp_path, source)
    found = signature_at(
        analysis,
        source.replace("make<Box<Box<i32>>>(0)",
                       "make<Box<Box<i32>>>(|)"),
    )
    assert found.signatures[0].label == "@macro make<T>(value)"
    assert found.active_parameter == 0


def test_signature_help_uses_the_innermost_call(tmp_path):
    """Nested calls and literal commas select the innermost parameter."""
    source = """\
fn inner(x: i32, y: i32) -> i32 { return x + y; }
fn outer(values: i32[], value: i32) -> i32 { return value; }
fn main() -> i32 { return outer([1, 2], inner(1, 2)); }
"""
    analysis, _ = unit(tmp_path, source)
    found = signature_at(
        analysis,
        source.replace("inner(1, 2)", "inner(1, |)"),
    )
    assert found.active_parameter == 1
    assert found.signatures[0].label == \
        "fn inner(x: i32, y: i32) -> i32"


def test_signature_help_lists_overloads_and_selects_by_argument(tmp_path):
    """Every overload is shown, with one accepting the active argument."""
    source = """\
fn pick(x: i32) -> i32 { return x; }
fn pick(x: i32, y: i32) -> i32 { return x + y; }
fn main() -> i32 { return pick(1, 2); }
"""
    analysis, _ = unit(tmp_path, source)
    found = signature_at(
        analysis,
        source.replace("pick(1, 2)", "pick(1, |)"),
    )
    assert [candidate.label for candidate in found.signatures] == [
        "fn pick(x: i32) -> i32",
        "fn pick(x: i32, y: i32) -> i32",
    ]
    assert found.active_signature == 1
    assert found.active_parameter == 1


def test_signature_help_resolves_imported_callables(tmp_path):
    """Imported package-style functions and macros expose their parameters."""
    write(tmp_path / "widgets.sie", """\
fn contains(widget: opaque*, x: f64, y: f64) -> bool { return true; }
@macro clamp(value, low, high) = value;
""")
    source = """\
import { contains, clamp } from widgets;
fn main() -> i32 {
    contains(null, 1.0, 2.0);
    return clamp(42, 0, 100);
}
"""
    analysis, _ = unit(tmp_path, source)

    function = signature_at(
        analysis,
        source.replace("contains(null, 1.0, 2.0)",
                       "contains(null, 1.0, |)"),
    )
    assert function.signatures[0].parameters == (
        "widget: opaque*", "x: f64", "y: f64")
    assert function.active_parameter == 2

    macro = signature_at(
        analysis,
        source.replace("clamp(42, 0, 100)", "clamp(42, 0, |)"),
    )
    assert macro.signatures[0].parameters == ("value", "low", "high")
    assert macro.active_parameter == 2


def test_server_registers_signature_help_triggers():
    """Opening a call and advancing an argument request signature help."""
    from lsprotocol import types

    from siec.lsp import create_server

    server = create_server()
    options = server.protocol.fm.feature_options[
        types.TEXT_DOCUMENT_SIGNATURE_HELP]
    assert options == types.SignatureHelpOptions(
        trigger_characters=["(", ","], retrigger_characters=[","])


def test_complete_lists_locals_visible_names_and_modules(tmp_path):
    """Lexical completion combines compiler scope and module bindings."""
    write(tmp_path / "util.sie", "fn answer() -> i32 { return 42; }")
    analysis, _ = unit(tmp_path, """\
import util;
fn helper() -> i32 { return 1; }
fn main() -> i32 {
    let count: i32 = 42;
    return count;
}
""")
    edited = """\
import util;
fn helper() -> i32 { return 1; }
fn main() -> i32 {
    let count: i32 = 42;
    cou
    return count;
}
"""

    locals_ = complete(analysis, edited, 4, 7)
    assert [(item.label, item.kind, item.detail) for item in locals_] == [
        ("count", "variable", "count: i32")]

    names = {item.label: item for item in complete(analysis, edited, 4, 4)}
    assert names["helper"].kind == "function"
    assert names["util"].kind == "module"
    assert names["Integer"].kind == "interface"
    assert names["SignedInteger"].kind == "interface"
    assert names["UnsignedInteger"].kind == "interface"
    assert names["i128"].kind == "keyword"
    assert names["u128"].kind == "keyword"
    assert names["return"].kind == "keyword"


def test_complete_lists_struct_fields_inside_an_aggregate(tmp_path):
    """Named aggregate literals complete the expected struct's fields."""
    analysis, _ = unit(tmp_path, """\
struct Point { x: i32; y: i32; }

fn main() -> i32 {
    let point: Point = { x = 1, y = 2 };
    return point.x;
}
""")
    edited = """\
struct Point { x: i32; y: i32; }

fn main() -> i32 {
    let point: Point = {
    return 0;
}
"""

    items = {item.label: item for item in complete(analysis, edited, 3, 24)}
    assert set(items) == {"x", "y"}
    assert items["x"].kind == "field"
    assert items["x"].detail == "x: i32"
    assert items["y"].detail == "y: i32"

    after_field = """\
struct Point { x: i32; y: i32; }

fn main() -> i32 {
    let point: Point = { x = 1, 
    return 0;
}
"""
    remaining = complete(analysis, after_field, 3, len("    let point: Point = { x = 1, "))
    assert [item.label for item in remaining] == ["y"]

    filtered = complete(
        analysis,
        after_field.replace("{ x = 1, ", "{ x = 1, y"),
        3,
        len("    let point: Point = { x = 1, y"),
    )
    assert [item.label for item in filtered] == ["y"]


def test_complete_lists_fields_for_an_assigned_aggregate(tmp_path):
    """Assignment aggregates resolve the target's type for field names."""
    analysis, _ = unit(tmp_path, """\
struct Header { size: u64; free: bool; }

fn main() -> i32 {
    let header: Header = { size = 0, free = false };
    return 0;
}
""")
    edited = """\
struct Header { size: u64; free: bool; }

fn main() -> i32 {
    let header: Header = { size = 0, free = false };
    header = {
    return 0;
}
"""

    items = {item.label: item for item in complete(analysis, edited, 4, 14)}
    assert set(items) == {"size", "free"}
    assert items["size"].detail == "size: u64"

    through_ptr = """\
struct Header { size: u64; free: bool; }

fn init(header: Header*) {
    *header = {
}
fn main() -> i32 { return 0; }
"""
    analysis, _ = unit(tmp_path, """\
struct Header { size: u64; free: bool; }

fn init(header: Header*) {
    *header = { size = 0, free = false };
}
fn main() -> i32 { return 0; }
""")
    items = {
        item.label: item
        for item in complete(analysis, through_ptr, 3, len("    *header = {"))
    }
    assert set(items) == {"size", "free"}


def test_complete_lists_value_fields_and_methods(tmp_path):
    """A typed value's dot completion includes its fields and methods."""
    analysis, _ = unit(tmp_path, """\
struct Box { value: i32; }
fn Box::answer(const &self) -> i32 { return self.value; }

fn main() -> i32 {
    let box: Box = { 42 };
    return box.answer();
}
""")
    edited = """\
struct Box { value: i32; }
fn Box::answer(const &self) -> i32 { return self.value; }

fn main() -> i32 {
    let box: Box = { 42 };
    box.
    return 0;
}
"""

    items = {item.label: item for item in complete(analysis, edited, 5, 8)}
    assert items["value"].kind == "field"
    assert items["value"].detail == "value: i32"
    assert items["answer"].kind == "method"
    assert items["answer"].detail == \
        "fn Box::answer(const &Box) -> i32"


def test_complete_lists_methods_on_static_call_result(tmp_path):
    """Dot completion parses a static call result as its receiver."""
    analysis, _ = unit(tmp_path, """\
struct Box {
    value: i32;
    fn init(&self, value: i32) { self.value = value; }
    fn make() -> Box { return Box(42); }
    fn answer(const &self) -> i32 { return self.value; }
}

fn main() -> i32 { return Box::make().answer(); }
""")
    edited = """\
struct Box {
    value: i32;
    fn init(&self, value: i32) { self.value = value; }
    fn make() -> Box { return Box(42); }
    fn answer(const &self) -> i32 { return self.value; }
}

fn main() -> i32 { return Box::make().an }
"""

    items = complete(analysis, edited, 7, len(edited.splitlines()[7]) - 2)
    assert [item.label for item in items] == ["answer"]


def test_complete_lists_pointer_fields_and_methods(tmp_path):
    """Arrow completion after p-> offers the pointee's fields and methods."""
    analysis, _ = unit(tmp_path, """\
struct Box { value: i32; }
fn Box::answer(const &self) -> i32 { return self.value; }

fn main() -> i32 {
    let box: Box = { 42 };
    let ptr: Box* = &box;
    return ptr->answer();
}
""")
    edited = """\
struct Box { value: i32; }
fn Box::answer(const &self) -> i32 { return self.value; }

fn main() -> i32 {
    let box: Box = { 42 };
    let ptr: Box* = &box;
    ptr->
    return 0;
}
"""

    items = {item.label: item for item in complete(analysis, edited, 6, 9)}
    assert items["value"].kind == "field"
    assert items["value"].detail == "value: i32"
    assert items["answer"].kind == "method"
    assert items["answer"].detail == \
        "fn Box::answer(const &Box) -> i32"

    filtered = complete(analysis, edited.replace("ptr->", "ptr->va"), 6, 11)
    assert [item.label for item in filtered] == ["value"]


def test_complete_lists_chained_arrow_members(tmp_path):
    """Arrow completion follows pointer chains like a->b->."""
    analysis, _ = unit(tmp_path, """\
struct Node {
    next: Node*;
    value: i32;
}

fn main() -> i32 {
    let node: Node = { next = null, value = 1 };
    let ptr: Node* = &node;
    return ptr->next->value;
}
""")
    edited = """\
struct Node {
    next: Node*;
    value: i32;
}

fn main() -> i32 {
    let node: Node = { next = null, value = 1 };
    let ptr: Node* = &node;
    ptr->next->
    return 0;
}
"""

    items = {item.label: item for item in complete(
        analysis, edited, 8, len("    ptr->next->"))}
    assert set(items) == {"next", "value"}
    assert items["next"].kind == "field"
    assert items["value"].detail == "value: i32"

    filtered = complete(
        analysis, edited.replace("ptr->next->", "ptr->next->v"),
        8, len("    ptr->next->v"))
    assert [item.label for item in filtered] == ["value"]

    through_field = edited.replace("ptr->next->", "node.next->")
    items = {item.label: item for item in complete(
        analysis, through_field, 8, len("    node.next->"))}
    assert set(items) == {"next", "value"}


def test_complete_lists_enum_members(tmp_path):
    """Scoped completion after Type:: offers that enum's members."""
    analysis, _ = unit(tmp_path, """\
enum Color {
    RED,
    BLUE = 7,
}

fn main() -> i32 {
    return Color::BLUE as i32;
}
""")
    edited = """\
enum Color {
    RED,
    BLUE = 7,
}

fn main() -> i32 {
    Color::
    return 0;
}
"""

    items = {item.label: item for item in complete(analysis, edited, 6, 11)}
    assert set(items) == {"BLUE", "RED"}
    assert items["BLUE"].kind == "enumMember"
    assert items["BLUE"].detail == "Color::BLUE = 7"
    assert items["RED"].kind == "enumMember"
    assert items["RED"].detail == "Color::RED = 0"
    assert "UnsignedInteger" not in items

    filtered = complete(analysis, edited.replace("Color::", "Color::B"), 6, 12)
    assert [item.label for item in filtered] == ["BLUE"]


def test_complete_lists_type_methods_after_scope(tmp_path):
    """Scoped completion after Struct:: offers that type's methods."""
    analysis, _ = unit(tmp_path, """\
struct Box { value: i32; }
fn Box::answer(const &self) -> i32 { return self.value; }

fn main() -> i32 {
    let box: Box = { 42 };
    return box.answer();
}
""")
    edited = """\
struct Box { value: i32; }
fn Box::answer(const &self) -> i32 { return self.value; }

fn main() -> i32 {
    let box: Box = { 42 };
    Box::
    return 0;
}
"""

    items = {item.label: item for item in complete(analysis, edited, 5, 9)}
    assert items["answer"].kind == "method"
    assert items["answer"].detail == \
        "fn Box::answer(const &Box) -> i32"


def test_complete_type_context_excludes_value_names(tmp_path):
    """A type annotation offers types without unrelated value declarations."""
    analysis, _ = unit(tmp_path, """\
struct Box { value: i32; }
fn helper() -> i32 { return 1; }
fn main() -> i32 { let box: Box; return box.value; }
""")
    edited = """\
struct Box { value: i32; }
fn helper() -> i32 { return 1; }
fn main() -> i32 { let other:  }
"""

    items = {item.label: item for item in complete(analysis, edited, 2, 31)}
    assert items["Box"].kind == "struct"
    assert items["Integer"].kind == "interface"
    assert items["SignedInteger"].kind == "interface"
    assert items["UnsignedInteger"].kind == "interface"
    assert items["i32"].kind == "keyword"
    assert "helper" not in items


def test_inspect_resolves_an_imported_module(tmp_path):
    """
    Hovering an import's path names the module and sites its file, so
    the import itself navigates; a qualified use's prefix reads the same.
    """
    write(tmp_path / "util.sie", """\
fn add(x: i32, y: i32) -> i32 { return x + y; }
""")

    analysis, src = unit(tmp_path, """\
import util;

fn main() -> i32 {
    return util.add(40, 2);
}
""")

    module = [(str((tmp_path / "util.sie").resolve()), 1)]

    finding = probe(analysis, src, 0, 9)
    assert finding.text == "import util;"
    assert finding.targets == module

    prefix = probe(analysis, src, 3, 12)
    assert prefix.text == "import util;"
    assert prefix.targets == module


def test_inspect_shows_a_struct_declaration(tmp_path):
    """
    Hovering a type name renders the struct with its fields.
    """
    analysis, src = unit(tmp_path, """\
struct Point { x: i32; y: i32; }

fn main() -> i32 {
    let p: Point;
    return p.x;
}
""")

    finding = probe(analysis, src, 3, 11)
    assert finding.text == "struct Point {\n    x: i32;\n    y: i32;\n}"
    assert finding.targets == [(str(src.resolve()), 1)]


def test_inspect_misses_off_any_name(tmp_path):
    """
    Positions on literals, operators, or blanks resolve to nothing.
    """
    analysis, src = unit(tmp_path, """\
fn main() -> i32 {
    return 40 + 2;
}
""")

    assert probe(analysis, src, 1, 12) is None
    assert probe(analysis, src, 0, 0) is None


def test_outline_names_macros_apart_from_constants():
    """
    '@macro' substitutes rather than stores, so the outline says so.
    """
    from siec.lsp import outline

    symbols = outline("""
        @const WIDTH = 8;
        @macro errno = 42;
        @macro twice(v) = v + v;
        @macro converted<T>(v) = v as T;
    """)

    assert [(s.name, s.kind) for s in symbols] == [
        ("WIDTH", "constant"),
        ("errno", "macro"),
        ("twice", "macro"),
        ("converted", "macro"),
    ]
