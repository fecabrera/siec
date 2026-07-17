"""Tests for siec.cli."""

import subprocess
import sys

from siec.cli import format_error, main


def run_cli(monkeypatch, *argv):
    """
    Invoke the compiler's main() with the given command-line arguments.
    """
    monkeypatch.setattr(sys, "argv", ["siec", *map(str, argv)])
    return main()


def test_emit_llvm_prints_the_module(tmp_path, capsys, monkeypatch):
    """
    --emit-llvm prints the module's IR instead of building.
    """
    src = tmp_path / "p.sie"
    src.write_text("fn main() -> i32 { return 0; }")
    assert run_cli(monkeypatch, src, "--emit-llvm") == 0
    out = capsys.readouterr().out
    assert 'define i32 @"main"' in out


def test_compiles_and_links_an_executable(tmp_path, monkeypatch):
    """
    The default pipeline produces a runnable executable at -o.
    """
    src = tmp_path / "p.sie"
    src.write_text("fn main() -> i32 { return 5; }")
    exe = tmp_path / "p"
    assert run_cli(monkeypatch, src, "-o", exe) == 0
    assert subprocess.run([str(exe)]).returncode == 5


def test_compiles_multiple_sources_together(tmp_path, monkeypatch):
    """
    Several source files on the command line build into one executable.
    """
    main_src = tmp_path / "main.sie"
    main_src.write_text("fn helper() -> i32; fn main() -> i32 { return helper(); }")
    impl = tmp_path / "impl.sie"
    impl.write_text("fn helper() -> i32 { return 3; }")
    exe = tmp_path / "p"
    assert run_cli(monkeypatch, main_src, impl, "-o", exe) == 0
    assert subprocess.run([str(exe)]).returncode == 3


def test_lib_next_to_the_source_is_on_the_include_path(tmp_path, monkeypatch):
    """
    Includes resolve through the lib/ directory beside the source file by default.
    """
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "dep.sie").write_text("fn dep() -> i32 { return 2; }")
    src = tmp_path / "p.sie"
    src.write_text('@include("dep") fn main() -> i32 { return dep(); }')
    exe = tmp_path / "p"
    assert run_cli(monkeypatch, src, "-o", exe) == 0
    assert subprocess.run([str(exe)]).returncode == 2


def test_include_flag_adds_search_paths(tmp_path, monkeypatch, capsys):
    """
    -I directories are searched when resolving includes.
    """
    inc = tmp_path / "vendor"
    inc.mkdir()
    (inc / "dep.sie").write_text("fn dep() -> i32 { return 1; }")
    src = tmp_path / "p.sie"
    src.write_text('@include("dep") fn main() -> i32 { return dep(); }')
    assert run_cli(monkeypatch, src, "-I", inc, "--emit-llvm") == 0
    assert "dep" in capsys.readouterr().out


def test_format_error_uses_the_line_attribute():
    """
    A codegen error carrying 'sie_line' renders as '<source> at line <n>: <message>'.
    """
    error = TypeError("undefined variable 'x'")
    error.sie_line = 7
    assert format_error("p.sie", error) == "p.sie at line 7: undefined variable 'x'"


def test_format_error_parses_the_line_prefix():
    """
    A lexer or parser error renders its embedded 'line <n>:' as the location.
    """
    error = SyntaxError("line 3: expected ';', got '}'")
    assert format_error("p.sie", error) == "p.sie at line 3: expected ';', got '}'"


def test_format_error_without_a_line():
    """
    An error with no line information renders as '<source>: <message>'.
    """
    error = FileNotFoundError("cannot resolve include 'x'")
    assert format_error("p.sie", error) == "p.sie: cannot resolve include 'x'"


def test_format_error_prefers_the_errors_own_file():
    """
    An error tagged with its own file names that file, not the command-line source.
    """
    error = NameError("undefined variable 'x'")
    error.sie_line = 4
    error.sie_file = "lib/dep.sie"
    assert format_error("app.sie", error) == "lib/dep.sie at line 4: undefined variable 'x'"


def test_codegen_error_is_reported_with_a_line(tmp_path, monkeypatch, capsys):
    """
    A codegen error exits non-zero and prints the source and line, not a traceback.
    """
    src = tmp_path / "p.sie"
    src.write_text("fn main() -> i32 {\n    return missing;\n}\n")
    assert run_cli(monkeypatch, src, "-o", tmp_path / "p") == 1

    err = capsys.readouterr().err
    assert "at line 2: undefined variable 'missing'" in err
    assert "Traceback" not in err


def test_widening_error_reports_the_declaration_line(tmp_path, monkeypatch, capsys):
    """
    The implicit-conversion error points at the offending statement's line.
    """
    src = tmp_path / "p.sie"
    src.write_text("fn main() -> i32 {\n    let a: i32 = 0;\n    let b: u32 = a;\n    return 0;\n}\n")
    assert run_cli(monkeypatch, src, "-o", tmp_path / "p") == 1

    err = capsys.readouterr().err
    assert "at line 3:" in err
    assert "Traceback" not in err


def test_parse_error_is_reported_without_a_traceback(tmp_path, monkeypatch, capsys):
    """
    A parse error exits non-zero with a readable message and no traceback.
    """
    src = tmp_path / "p.sie"
    src.write_text("fn main() -> i32 {\n    return 1\n}\n")
    assert run_cli(monkeypatch, src, "-o", tmp_path / "p") == 1

    err = capsys.readouterr().err
    assert "at line" in err
    assert "Traceback" not in err


def test_error_in_an_included_file_names_that_file(tmp_path, monkeypatch, capsys):
    """
    A compile error inside an included file reports that file, not the includer.
    """
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "dep.sie").write_text("fn dep() -> i32 {\n    return missing;\n}\n")
    src = tmp_path / "app.sie"
    src.write_text('@include("dep")\nfn main() -> i32 { return dep(); }\n')
    assert run_cli(monkeypatch, src, "-o", tmp_path / "p") == 1

    err = capsys.readouterr().err
    assert "dep.sie at line 2: undefined variable 'missing'" in err
    assert "app.sie" not in err
    assert "Traceback" not in err
