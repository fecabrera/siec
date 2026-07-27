"""Command-line driver for the Sie package manager.

`sie` is the project-level tool, next to `siec` for one compilation and
`sie-lsp` for an editor: it works from a package's manifest rather than
from a list of sources.

With no command it reads a package's manifest and shows it; 'install'
copies a package into the install root, where a build can find it, and
'list' says what is there.
"""

import argparse
import datetime
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

from siec.codegen.errors import display_path

MANIFEST = "package.toml"

# where installed packages live, unless SIE_PATH says otherwise
DEFAULT_SIE_PATH = Path.home() / ".sie"

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


def package_manifest(target: str) -> Path:
    """
    The manifest of the package at a path: its '<path>/package.toml'.

    Unlike the path a manifest is shown from, this one is a package and so
    a directory: what is copied out of it is named relative to it.

    Raises FileNotFoundError with the message to report.
    """
    path = Path(target)

    if path.is_dir():
        manifest = path / MANIFEST
        if not manifest.is_file():
            raise FileNotFoundError(f"no {MANIFEST!r} in {display_path(str(path))!r}")

        return manifest

    if path.exists():
        raise FileNotFoundError(f"{display_path(str(path))!r} is not a package "
                                "directory")

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


def read_manifest(manifest: Path) -> dict:
    """
    Parse a manifest.

    Raises ValueError carrying the message to report, so a caller does not
    have to tell an unreadable file from an ill-formed one.
    """
    try:
        return tomllib.loads(manifest.read_text())
    except OSError as error:
        raise ValueError(f"{display_path(str(manifest))}: {error.strerror}") from error
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{display_path(str(manifest))}: {error}") from error


def install_root() -> Path:
    """
    Where installed packages go: '$SIE_PATH/lib', with SIE_PATH falling
    back to '~/.sie' when it is not set.
    """
    configured = os.environ.get("SIE_PATH")
    base = Path(configured).expanduser() if configured else DEFAULT_SIE_PATH

    return base / "lib"


def identity(manifest: Path, data: dict) -> tuple[str, str]:
    """
    The name and version a manifest declares, which is what an install is
    filed under.

    Raises ValueError when either is missing: without both there is no
    directory to install into.
    """
    table = data.get("package") or {}

    missing = [key for key in ("name", "version") if not table.get(key)]
    if missing:
        raise ValueError(f"{display_path(str(manifest))}: "
                         f"[package] declares no "
                         + " or ".join(repr(key) for key in missing))

    return table["name"], table["version"]


def listed(value: object) -> list[str]:
    """
    A manifest entry that may be written as one string or as a list of
    them, as a list either way.
    """
    if isinstance(value, str):
        return [value]

    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, str)]

    return []


def contents(package: Path, data: dict) -> list[str]:
    """
    What an install carries: the manifest, whatever documents it points
    at, and the sources it declares.

    Raises ValueError on an entry that would reach outside the package,
    which no manifest has business naming.
    """
    table = data.get("package") or {}

    entries = [MANIFEST]
    entries.extend(listed(table.get("readme")))
    entries.extend(listed(table.get("license-files")))
    entries.extend(listed(table.get("sources")))

    for entry in entries:
        path = Path(entry)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"{display_path(str(package / MANIFEST))}: "
                             f"{entry!r} reaches outside the package")

    # a name repeated across the keys is still copied once
    seen: dict[str, None] = {}
    for entry in entries:
        seen.setdefault(entry.rstrip("/") or entry, None)

    return list(seen)


def copy_into(package: Path, entries: list[str], target: Path) -> list[str]:
    """
    Copy each entry from the package into `target`, keeping the place it
    holds inside the package. Answers what was copied, marking directories.

    An entry the manifest names but the package does not have is skipped
    with a warning: the install is still worth having.
    """
    copied = []

    for entry in entries:
        source = package / entry
        destination = target / entry

        destination.parent.mkdir(parents=True, exist_ok=True)

        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
            copied.append(entry + "/")
        elif source.is_file():
            shutil.copy2(source, destination)
            copied.append(entry)
        else:
            print(f"sie: warning: {display_path(str(source))} is not there, "
                  "skipping", file=sys.stderr)

    return copied


def replace(staging: Path, target: Path) -> None:
    """
    Put a freshly staged install in place of whatever was there.

    The old one only goes once the new one is complete, so a copy that
    fails halfway leaves the previous install untouched.
    """
    previous = target.with_name(target.name + ".replaced")
    shutil.rmtree(previous, ignore_errors=True)

    if target.exists():
        target.rename(previous)

    try:
        staging.rename(target)
    except OSError:
        if previous.exists():
            previous.rename(target)
        raise

    shutil.rmtree(previous, ignore_errors=True)


def install(argv: list[str]) -> int:
    """
    Copy a package into the install root, under its name and version.
    """
    args = argparse.ArgumentParser(
        prog="sie install",
        description="Install a package into $SIE_PATH/lib")
    args.add_argument("path", nargs="?", default=".",
                      help="the package to pull from (the working directory "
                           "by default)")
    opts = args.parse_args(argv)

    try:
        manifest = package_manifest(opts.path)
    except FileNotFoundError as error:
        print(f"sie: {error}", file=sys.stderr)

        # '<name>@<version>' is what an install is filed under, so it is
        # the natural thing to reach for; there is nowhere to fetch it from
        if "@" in opts.path:
            print("sie: there is no registry to pull from yet: give the path "
                  "to the package", file=sys.stderr)

        return 1

    package = manifest.parent

    try:
        data = read_manifest(manifest)
        name, version = identity(manifest, data)
        entries = contents(package, data)
    except ValueError as error:
        print(f"sie: {error}", file=sys.stderr)
        return 1

    if not (data.get("package") or {}).get("sources"):
        print(f"sie: warning: {display_path(str(manifest))} declares no "
              "'sources', so only its manifest is installed", file=sys.stderr)

    target = install_root() / f"{name}@{version}"
    staging = target.with_name("." + target.name + ".partial")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()

        copied = copy_into(package, entries, staging)
        replaced = target.exists()
        replace(staging, target)
    except OSError as error:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"sie: {error.filename or target}: {error.strerror}",
              file=sys.stderr)
        return 1

    print(f"{'replaced' if replaced else 'installed'} {name}@{version} "
          f"from {display_path(str(package))}")
    print(f"  {target}")

    for entry in copied:
        print(f"    {entry}")

    return 0


def natural_key(text: str) -> tuple:
    """
    A sort key that reads runs of digits as numbers, so '2.0' comes before
    '10.0' rather than after it.
    """
    return tuple((int(part), "") if part.isdigit() else (0, part)
                 for part in re.split(r"(\d+)", text) if part)


def installed() -> list[tuple[str, str, Path]]:
    """
    Every package in the install root, as its name, version, and where it
    sits, ordered by name and then by version.

    The directory name is the identity: what is installed is whatever was
    filed under a '<name>@<version>', whether or not its manifest can
    still be read.
    """
    root = install_root()
    if not root.is_dir():
        return []

    packages = []
    for entry in root.iterdir():
        # a staged copy is not an install; it is named so as to be skipped
        if not entry.is_dir() or entry.name.startswith("."):
            continue

        name, separator, version = entry.name.partition("@")
        if separator and name and version:
            packages.append((name, version, entry))

    return sorted(packages, key=lambda p: (natural_key(p[0]), natural_key(p[1])))


def description(package: Path) -> str:
    """
    What an installed package says it is, or nothing when its manifest
    does not say or cannot be read.
    """
    try:
        data = read_manifest(package / MANIFEST)
    except ValueError:
        return ""

    return str((data.get("package") or {}).get("description") or "")


def listing(argv: list[str]) -> int:
    """
    Print what is installed.
    """
    args = argparse.ArgumentParser(
        prog="sie list",
        description="List the packages installed in $SIE_PATH/lib")
    args.parse_args(argv)

    packages = installed()
    if not packages:
        print(f"sie: nothing installed in {install_root()}", file=sys.stderr)
        return 0

    width = max(len(f"{name}@{version}") for name, version, _ in packages)

    for name, version, package in packages:
        spec = f"{name}@{version}"
        text = description(package)
        print(f"{spec:<{width}}  {text}".rstrip())

    return 0


def show(argv: list[str]) -> int:
    """
    Read the selected package's manifest and print it.
    """
    args = argparse.ArgumentParser(
        prog="sie", description="Sie package manager",
        epilog="commands:\n"
               "  install [path]   copy a package into $SIE_PATH/lib\n"
               "  list             list what is installed there\n\n"
               "run 'sie <command> --help' for a command's own options",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    args.add_argument("path", nargs="?", default=".",
                      help=f"the package directory, or a {MANIFEST} itself "
                           "(the working directory by default)")
    opts = args.parse_args(argv)

    try:
        manifest = manifest_path(opts.path)
        data = read_manifest(manifest)
    except (FileNotFoundError, ValueError) as error:
        print(f"sie: {error}", file=sys.stderr)
        return 1

    print(display_path(str(manifest)))

    body = render(data)
    if body:
        print()
        print(body)

    return 0


COMMANDS = {"install": install, "list": listing}


def main() -> int:
    """
    Dispatch on the command, a path to show being the wordless one.
    """
    argv = sys.argv[1:]

    if argv and argv[0] in COMMANDS:
        return COMMANDS[argv[0]](argv[1:])

    return show(argv)


if __name__ == "__main__":
    sys.exit(main())
