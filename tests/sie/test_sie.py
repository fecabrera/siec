"""Tests for siec.sie, the package manager's command line."""

import sys
import tomllib

from siec.sie import main


def run_sie(monkeypatch, *argv):
    """
    Invoke the tool's main() with the given command-line arguments.
    """
    monkeypatch.setattr(sys, "argv", ["sie", *map(str, argv)])
    return main()


def write(tmp_path, text):
    """
    Put a manifest in a directory and answer that directory.
    """
    (tmp_path / "package.toml").write_text(text)
    return tmp_path


def test_reads_the_manifest_of_the_working_directory(tmp_path, capsys, monkeypatch):
    """
    With no path, the manifest comes from the working directory.
    """
    write(tmp_path, '[package]\nname = "here"\nversion = "1.0.0"\n')
    monkeypatch.chdir(tmp_path)

    assert run_sie(monkeypatch) == 0

    out = capsys.readouterr().out
    assert "package.toml" in out
    assert '[package]' in out
    assert 'name    = "here"' in out


def test_reads_the_manifest_of_a_named_directory(tmp_path, capsys, monkeypatch):
    """
    A directory selects the package under it, not the one in the
    working directory.
    """
    write(tmp_path, '[package]\nname = "root"\n')

    inner = tmp_path / "packages" / "core"
    inner.mkdir(parents=True)
    write(inner, '[package]\nname = "core"\n')

    monkeypatch.chdir(tmp_path)

    assert run_sie(monkeypatch, "packages/core") == 0

    out = capsys.readouterr().out
    assert '"core"' in out
    assert '"root"' not in out


def test_a_named_file_is_read_as_the_manifest(tmp_path, capsys, monkeypatch):
    """
    Naming a file reads that file, whatever it is called.
    """
    manifest = tmp_path / "other.toml"
    manifest.write_text('[package]\nname = "named"\n')

    assert run_sie(monkeypatch, manifest) == 0
    assert '"named"' in capsys.readouterr().out


def test_the_output_is_the_manifest_it_read(tmp_path, capsys, monkeypatch):
    """
    What is printed parses back into what was on disk: the tool shows
    the manifest, it does not summarize it.
    """
    source = """\
edition = 2026

[package]
name = "demo"
version = "1.0.0"
sources = ["src/"]
strict = true

[package.metadata]
homepage = "https://example.com"

[[bin]]
name = "one"

[[bin]]
name = "two"

[dependencies]
libc = "~1"
"""

    directory = write(tmp_path, source)

    assert run_sie(monkeypatch, directory) == 0

    body = capsys.readouterr().out.split("\n", 1)[1]
    assert tomllib.loads(body) == tomllib.loads(source)


def test_arrays_too_wide_to_read_break_over_lines(tmp_path, capsys, monkeypatch):
    """
    A long array is listed an entry per line rather than run off the side.
    """
    entries = [f"packages/{name}/src" for name in
               ("libc", "posix", "core", "mpdecimal", "zlib", "openssl")]
    source = "[package]\ninclude = [" + ", ".join(f'"{e}"' for e in entries) + "]\n"

    assert run_sie(monkeypatch, write(tmp_path, source)) == 0

    out = capsys.readouterr().out
    assert "include = [\n" in out
    assert '    "packages/libc/src",\n' in out
    assert tomllib.loads(out.split("\n", 1)[1]) == tomllib.loads(source)


def test_a_short_array_stays_on_its_line(tmp_path, capsys, monkeypatch):
    """
    One that fits is left inline, the way it was written.
    """
    source = '[package]\nlibs = ["ssl", "crypto"]\n'

    assert run_sie(monkeypatch, write(tmp_path, source)) == 0
    assert 'libs = ["ssl", "crypto"]' in capsys.readouterr().out


def test_values_keep_their_types(tmp_path, capsys, monkeypatch):
    """
    Numbers, booleans, dates, and escaped strings each print in TOML's
    own spelling rather than Python's.
    """
    source = ('[package]\n'
              'count = 12\n'
              'ratio = 1.5\n'
              'strict = true\n'
              'released = 2026-07-26\n'
              'note = "a \\"quoted\\" word"\n')

    assert run_sie(monkeypatch, write(tmp_path, source)) == 0

    out = capsys.readouterr().out
    assert "count    = 12" in out
    assert "ratio    = 1.5" in out
    assert "strict   = true" in out
    assert "released = 2026-07-26" in out
    assert 'note     = "a \\"quoted\\" word"' in out
    assert tomllib.loads(out.split("\n", 1)[1]) == tomllib.loads(source)


def test_an_empty_table_keeps_its_header(tmp_path, capsys, monkeypatch):
    """
    A table with no keys still prints: nothing else would show it is there.
    """
    assert run_sie(monkeypatch, write(tmp_path, "[dependencies]\n")) == 0
    assert "[dependencies]" in capsys.readouterr().out


def test_a_directory_without_a_manifest_is_reported(tmp_path, capsys, monkeypatch):
    """
    A directory holding no manifest fails, naming what was missing.
    """
    assert run_sie(monkeypatch, tmp_path) == 1

    err = capsys.readouterr().err
    assert "package.toml" in err
    assert err.startswith("sie: ")


def test_a_path_that_is_not_there_is_reported(tmp_path, capsys, monkeypatch):
    """
    A path that does not exist fails without mentioning a manifest it
    never got to look for.
    """
    assert run_sie(monkeypatch, tmp_path / "nowhere") == 1
    assert "does not exist" in capsys.readouterr().err


def test_a_malformed_manifest_is_reported(tmp_path, capsys, monkeypatch):
    """
    A syntax error carries the parser's own message, and the file it
    was found in.
    """
    directory = write(tmp_path, "[package\nname = 1\n")

    assert run_sie(monkeypatch, directory) == 1

    err = capsys.readouterr().err
    assert "package.toml" in err
    assert "line 1" in err


def test_an_empty_manifest_prints_only_its_path(tmp_path, capsys, monkeypatch):
    """
    An empty manifest is valid TOML and reads as an empty package.
    """
    assert run_sie(monkeypatch, write(tmp_path, "")) == 0
    assert capsys.readouterr().out.strip().endswith("package.toml")
