"""Command-line driver for the Sie package manager.

`sie` is the project-level tool, next to `siec` for one compilation and
`sie-lsp` for an editor: it works from a package's manifest rather than
from a list of sources. For now it reads that manifest and shows it.
"""

import argparse
import datetime
import sys
import tomllib
from pathlib import Path

from siec.codegen.errors import display_path

MANIFEST = "package.toml"

# an array wider than this is listed an entry per line instead
INLINE_WIDTH = 72


def manifest_path(target: str) -> Path:
    """
    The manifest a path selects: the file itself when one is named, and
    the 'package.toml' inside it when a directory is.

    Raises FileNotFoundError with the message to report.
    """
    path = Path(target)

    if path.is_dir():
        manifest = path / MANIFEST
        if not manifest.is_file():
            raise FileNotFoundError(f"no {MANIFEST!r} in {display_path(str(path))!r}")

        return manifest

    if path.is_file():
        return path

    raise FileNotFoundError(f"{display_path(str(path))!r} does not exist")


def quote(text: str) -> str:
    """
    A TOML basic string: the escapes the format defines, and the six-digit
    form for anything else that cannot stand for itself.
    """
    escapes = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t",
               "\n": "\\n", "\f": "\\f", "\r": "\\r"}

    out = []
    for char in text:
        if char in escapes:
            out.append(escapes[char])
        elif char < " " or char == "\x7f":
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)

    return '"' + "".join(out) + '"'


def render_value(value: object) -> str:
    """
    One value in TOML's own spelling, arrays inline.
    """
    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, str):
        return quote(value)

    if isinstance(value, (int, float)):
        return repr(value)

    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()

    if isinstance(value, list):
        return "[" + ", ".join(render_value(item) for item in value) + "]"

    return str(value)


def render_entry(key: str, value: object, width: int) -> list[str]:
    """
    A 'key = value' line, padded to `width` so a table's values line up.
    An array too wide to read that way spreads over a line per entry.
    """
    text = render_value(value)
    line = f"{key:<{width}} = {text}"

    if not isinstance(value, list) or len(line) <= INLINE_WIDTH or not value:
        return [line]

    return [f"{key:<{width}} = ["] \
        + [f"    {render_value(item)}," for item in value] \
        + ["]"]


def is_table(value: object) -> bool:
    return isinstance(value, dict)


def is_table_array(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(is_table(v) for v in value)


def render_table(header: str | None, table: dict) -> list[str]:
    """
    One table: its header, then the keys that hold plain values, aligned.
    Sub-tables are headers of their own and come after, so the output
    reads back as the file it came from.
    """
    entries = {k: v for k, v in table.items()
               if not is_table(v) and not is_table_array(v)}

    lines = [] if header is None else [f"[{header}]"]
    width = max((len(k) for k in entries), default=0)

    for key, value in entries.items():
        lines.extend(render_entry(key, value, width))

    return lines


def render(data: dict) -> str:
    """
    The whole manifest, table by table in the order it was written.
    """
    blocks: list[list[str]] = []

    def walk(table: dict, prefix: str | None) -> None:
        block = render_table(prefix, table)

        # a header alone says nothing when every key under it is a table of
        # its own: those headers stand for it. An empty table keeps its own,
        # since nothing else would show that it is there
        header_only = prefix is not None and len(block) == 1

        if block and not (header_only and table):
            blocks.append(block)

        for key, value in table.items():
            path = key if prefix is None else f"{prefix}.{key}"

            if is_table(value):
                walk(value, path)
            elif is_table_array(value):
                for element in value:
                    blocks.append([f"[[{path}]]", *render_table(None, element)])
                    for k, v in element.items():
                        if is_table(v):
                            walk(v, f"{path}.{k}")

    walk(data, None)

    return "\n\n".join("\n".join(block) for block in blocks)


def main() -> int:
    """
    Read the selected package's manifest and print it.
    """
    args = argparse.ArgumentParser(prog="sie",
                                   description="Sie package manager")
    args.add_argument("path", nargs="?", default=".",
                      help=f"the package directory, or a {MANIFEST} itself "
                           "(the working directory by default)")
    opts = args.parse_args()

    try:
        manifest = manifest_path(opts.path)
    except FileNotFoundError as error:
        print(f"sie: {error}", file=sys.stderr)
        return 1

    try:
        data = tomllib.loads(manifest.read_text())
    except OSError as error:
        print(f"sie: {display_path(str(manifest))}: {error.strerror}",
              file=sys.stderr)
        return 1
    except tomllib.TOMLDecodeError as error:
        print(f"sie: {display_path(str(manifest))}: {error}", file=sys.stderr)
        return 1

    print(display_path(str(manifest)))

    body = render(data)
    if body:
        print()
        print(body)

    return 0


if __name__ == "__main__":
    sys.exit(main())
