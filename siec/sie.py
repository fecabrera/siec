"""Command-line driver for the Sie package manager.

`sie` is the project-level tool, next to `siec` for one compilation and
`sie-lsp` for an editor: it works from a package's manifest rather than
from a list of sources.

With no command it reads a package's manifest and shows it. 'install'
copies a package into the install root, 'uninstall' removes one, and
'list' says what is there; 'build' compiles a package against what it
finds in the same place.
"""

import argparse
import datetime
import os
import re
import shutil
import sys
import tomllib
from collections import deque
from pathlib import Path

from siec.codegen.errors import display_path

MANIFEST = "package.toml"

# what a package can be: an app is built, a library installed
KINDS = ("app", "library")

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


def unit(manifest: Path, data: dict) -> tuple[str, dict]:
    """
    What kind of thing a manifest describes, and the table saying what it
    is made of: an '[app]', which is built, or a '[library]', which is
    installed for other packages to build against.

    '[package]' says who the package is; one of these two says what it is.

    Raises ValueError when a manifest declares both, or neither.
    """
    found = [(kind, data[kind]) for kind in KINDS
             if isinstance(data.get(kind), dict)]

    if len(found) == 1:
        return found[0]

    where = display_path(str(manifest))
    if found:
        raise ValueError(f"{where}: declares both [app] and [library]; "
                         "a package is one or the other")

    raise ValueError(f"{where}: declares neither [app] nor [library], so "
                     "there is nothing to build or install")


def contents(package: Path, data: dict) -> list[str]:
    """
    What an install carries: the manifest, whatever documents it points
    at, and the sources it declares.

    Raises ValueError on an entry that would reach outside the package,
    which no manifest has business naming.
    """
    manifest = package / MANIFEST
    table = data.get("package") or {}
    _, made_of = unit(manifest, data)

    entries = [MANIFEST]
    entries.extend(listed(table.get("readme")))
    entries.extend(listed(table.get("license-files")))
    entries.extend(listed(made_of.get("sources")))

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
        kind, made_of = unit(manifest, data)

        # an app is the end of the line: it is built, and nothing builds
        # against it, so there is no reason for it to sit in the lib root
        if kind != "library":
            raise ValueError(f"{display_path(str(manifest))}: an [app] is "
                             "built, not installed")

        name, version = identity(manifest, data)
        entries = contents(package, data)
    except ValueError as error:
        print(f"sie: {error}", file=sys.stderr)
        return 1

    if not made_of.get("sources"):
        print(f"sie: warning: {display_path(str(manifest))} declares no "
              "[library] 'sources', so only its manifest is installed",
              file=sys.stderr)

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


def version_parts(version: str) -> tuple[int, ...]:
    """
    A version as the numbers it is made of, so they compare as numbers.
    Anything that is not a number stops the reading, which is enough to
    order releases and leaves pre-release tags out of the comparison.
    """
    parts = []
    for piece in re.split(r"[.\-+]", version):
        if not piece.isdigit():
            break
        parts.append(int(piece))

    return tuple(parts)


def satisfies(version: str, requirement: str) -> bool:
    """
    Whether an installed version answers a dependency's requirement.

    '*' takes anything, '~1' and '~1.2' allow later releases that keep the
    parts written down, '^1.2' allows anything up to the next version that
    may break, a comparison compares, and a bare version is that version.
    """
    requirement = requirement.strip()

    if not requirement or requirement == "*":
        return True

    for operator in (">=", "<=", "!=", "==", ">", "<", "="):
        if requirement.startswith(operator):
            wanted = version_parts(requirement[len(operator):].strip())
            found = version_parts(version)

            # compare over as many parts as the requirement writes down, so
            # '>= 1.2' reads 1.2.7 as 1.2
            if operator in ("==", "=", "!="):
                equal = found[:len(wanted)] == wanted
                return equal if operator != "!=" else not equal

            found = found[:len(wanted)] + (0,) * (len(wanted) - len(found))
            if operator == ">=":
                return found >= wanted
            if operator == "<=":
                return found <= wanted
            if operator == ">":
                return found > wanted

            return found < wanted

    if requirement[0] in "~^":
        wanted = version_parts(requirement[1:].strip())
        found = version_parts(version)

        if not wanted:
            return True

        # a tilde pins the major and the minor, or the major alone when
        # that is all it names; a caret pins up to the first part that is
        # not zero: both mean 'newer, but not different'
        if requirement[0] == "~":
            pinned = min(len(wanted), 2)
        else:
            pinned = next((i + 1 for i, part in enumerate(wanted) if part), len(wanted))

        return found[:pinned] == wanted[:pinned] and found >= wanted

    wanted = version_parts(requirement)

    return version_parts(version)[:len(wanted)] == wanted


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


def uninstall(argv: list[str]) -> int:
    """
    Remove an installed package, one version by an exact spec or every
    version by '--all'. A bare name is unambiguous only when one version
    of it is installed.
    """
    args = argparse.ArgumentParser(
        prog="sie uninstall",
        description="Remove packages from $SIE_PATH/lib")
    args.add_argument("package", metavar="name[@version]",
                      help="the installed package to remove")
    args.add_argument("-a", "--all", action="store_true",
                      help="remove every installed version of the package")
    opts = args.parse_args(argv)

    name, separator, version = opts.package.partition("@")
    if not name or (separator and not version):
        print(f"sie: invalid package spec {opts.package!r}: expected "
              "'name' or 'name@version'", file=sys.stderr)
        return 1

    if opts.all and separator:
        print("sie: '--all' takes a package name, not 'name@version'",
              file=sys.stderr)
        return 1

    matches = [package for package in installed() if package[0] == name]
    if separator:
        selected = [package for package in matches if package[1] == version]
        if not selected:
            print(f"sie: {name}@{version} is not installed", file=sys.stderr)
            return 1
    elif opts.all:
        selected = matches
        if not selected:
            print(f"sie: no {name} is installed", file=sys.stderr)
            return 1
    else:
        if not matches:
            print(f"sie: no {name} is installed", file=sys.stderr)
            return 1

        if len(matches) > 1:
            print("sie: multiple versions detected, specify one:",
                  file=sys.stderr)
            for installed_name, installed_version, _ in matches:
                print(f"  {installed_name}@{installed_version}", file=sys.stderr)
            return 1

        selected = matches

    for installed_name, installed_version, path in selected:
        try:
            # Never follow an install-root symlink while removing it.
            if path.is_symlink():
                path.unlink()
            else:
                shutil.rmtree(path)
        except OSError as error:
            print(f"sie: {error.filename or path}: {error.strerror}",
                  file=sys.stderr)
            return 1

        print(f"uninstalled {installed_name}@{installed_version}")

    return 0


class Resolved:
    """
    One package in a build: where it is, what it declares, and how it was
    asked for.
    """

    def __init__(self, name: str, version: str, path: Path, data: dict,
                 made_of: dict):
        self.name = name
        self.version = version
        self.path = path
        self.data = data

        # the '[app]' or '[library]' table: what the package is made of
        self.made_of = made_of

    @property
    def spec(self) -> str:
        return f"{self.name}@{self.version}" if self.version else self.name

    def source_dirs(self) -> list[Path]:
        """
        The directories this package's modules are imported from: what its
        'sources' names, which is what a build puts on the include path.
        """
        return [self.path / entry for entry in listed(self.made_of.get("sources"))
                if (self.path / entry).is_dir()]

    def source_files(self) -> list[Path]:
        """
        The units to compile: a 'sources' entry that is a file, and the
        '.sie' files directly inside one that is a directory.

        What sits deeper is reached through an import or an '@include',
        so compiling it again as its own unit would be wrong.
        """
        files = []
        for entry in listed(self.made_of.get("sources")):
            path = self.path / entry

            if path.is_file():
                files.append(path)
            elif path.is_dir():
                files.extend(sorted(path.glob("*.sie")))

        return files

    def libs(self) -> list[str]:
        return listed(self.made_of.get("libs"))

    def dependencies(self) -> dict[str, str]:
        table = self.data.get("dependencies") or {}

        return {name: requirement for name, requirement in table.items()
                if isinstance(requirement, str)}


def resolve(root: Resolved) -> list[Resolved]:
    """
    The packages a build needs, in the order they were reached: a package's
    own dependencies before theirs, so a dependent links ahead of what it
    depends on.

    A package asked for from two places is resolved once, against every
    requirement at once, so one build never holds two versions of it.

    Raises LookupError naming what could not be resolved and who wanted it.
    """
    available: dict[str, list[tuple[str, Path]]] = {}
    for name, version, path in installed():
        available.setdefault(name, []).append((version, path))

    for choices in available.values():
        choices.sort(key=lambda choice: natural_key(choice[0]), reverse=True)

    loaded: dict[Path, Resolved] = {}

    def package(name: str, version: str, path: Path) -> Resolved:
        if path in loaded:
            return loaded[path]

        try:
            data = read_manifest(path / MANIFEST)
            _, made_of = unit(path / MANIFEST, data)
        except ValueError as error:
            raise LookupError(str(error)) from error

        loaded[path] = Resolved(name, version, path, data, made_of)
        return loaded[path]

    def live_graph(chosen: dict[str, Resolved]):
        """
        Requirements and discovery order reachable through the versions
        currently chosen. Dependencies of an abandoned version disappear
        because that package is no longer traversed.
        """
        requirements: dict[str, list[tuple[str, str]]] = {}
        order: list[str] = []
        pending = deque([root])
        expanded = set()

        while pending:
            asker = pending.popleft()
            if asker is not root:
                if asker.name in expanded:
                    continue
                expanded.add(asker.name)

            for name, requirement in asker.dependencies().items():
                if name not in requirements:
                    requirements[name] = []
                    order.append(name)

                request = (requirement, asker.spec)
                if request not in requirements[name]:
                    requirements[name].append(request)

                if name in chosen and name not in expanded:
                    pending.append(chosen[name])

        return requirements, order

    seen = set()
    failure = None

    def search(chosen: dict[str, Resolved]) -> list[Resolved] | None:
        nonlocal failure

        requirements, order = live_graph(chosen)

        # Unreachable choices belong to a version branch already abandoned;
        # pruning them keeps their dependency sets out of subsequent states.
        chosen = {name: chosen[name] for name in order if name in chosen}
        state = tuple((name, chosen[name].version, chosen[name].path)
                      for name in order if name in chosen)
        if state in seen:
            return None
        seen.add(state)

        # Reconsider a selected package before resolving its descendants when
        # a newly discovered requirement no longer accepts its version.
        unresolved = next((
            name for name in order
            if name not in chosen
            or not all(satisfies(chosen[name].version, requirement)
                       for requirement, _ in requirements[name])
        ), None)

        if unresolved is None:
            return [chosen[name] for name in order]

        requests = requirements[unresolved]
        wanted = [requirement for requirement, _ in requests]
        candidates = [
            (version, path)
            for version, path in available.get(unresolved, ())
            if all(satisfies(version, requirement) for requirement in wanted)
        ]

        if not candidates:
            failure = unresolved, requests
            return None

        for version, path in candidates:
            attempt = dict(chosen)
            attempt[unresolved] = package(unresolved, version, path)
            if (resolved := search(attempt)) is not None:
                return resolved

        return None

    if (resolved := search({})) is not None:
        return resolved

    name, requests = failure
    asked = ", ".join(f"{requirement!r} by {who}"
                      for requirement, who in requests)
    have = sorted((version for version, _ in available.get(name, ())),
                  key=natural_key)
    raise LookupError(
        f"no installed {name} answers {asked}"
        + (f"; installed: {', '.join(have)}" if have
           else f"; no {name} is installed"))


def build(argv: list[str]) -> int:
    """
    Compile a package against what is installed, into its own build/.
    """
    args = argparse.ArgumentParser(
        prog="sie build",
        description="Build a package against its installed dependencies")
    args.add_argument("path", nargs="?", default=".",
                      help="the package to build (the working directory "
                           "by default)")
    args.add_argument("-O", default=0, type=int, choices=[0, 1, 2, 3], dest="opt",
                      metavar="N", help="optimization level, cc-style (default 0)")
    args.add_argument("-g", action="store_true", dest="debug",
                      help="emit DWARF debug info, for source-level debugging")
    opts = args.parse_args(argv)

    try:
        manifest = package_manifest(opts.path)
        data = read_manifest(manifest)
        kind, made_of = unit(manifest, data)

        # a library has no entry point: it is installed, and an app is
        # what turns it into something that runs
        if kind != "app":
            raise ValueError(f"{display_path(str(manifest))}: a [library] is "
                             "installed, not built")
    except (FileNotFoundError, ValueError) as error:
        print(f"sie: {error}", file=sys.stderr)
        return 1

    package = manifest.parent
    table = data.get("package") or {}

    name = table.get("name")
    if not name:
        print(f"sie: {display_path(str(manifest))}: [package] declares no "
              "'name', so the binary has nothing to be called", file=sys.stderr)
        return 1

    root = Resolved(name, str(table.get("version") or ""), package, data, made_of)

    sources = root.source_files()
    if not sources:
        print(f"sie: {display_path(str(manifest))}: no sources to build; "
              "[app] 'sources' names none", file=sys.stderr)
        return 1

    try:
        tree = resolve(root)
    except LookupError as error:
        print(f"sie: {error}", file=sys.stderr)
        return 1

    # the package's own directories come first, so a module of its own wins
    # over one a dependency happens to publish under the same name
    includes: list[str] = []
    libs: list[str] = []

    for member in (root, *tree):
        for directory in member.source_dirs():
            if str(directory) not in includes:
                includes.append(str(directory))

        for lib in member.libs():
            if lib not in libs:
                libs.append(lib)

    output = package / "build" / name
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"sie: {error.filename or output.parent}: {error.strerror}",
              file=sys.stderr)
        return 1

    print(f"building {root.spec}")
    for member in tree:
        print(f"  {member.spec}")

    command = [str(s) for s in sources]
    for directory in includes:
        command += ["-I", directory]
    for lib in libs:
        command += ["-l", lib]
    if opts.opt:
        command += [f"-O{opts.opt}"]
    if opts.debug:
        command += ["-g"]
    command += ["-o", str(output)]

    from siec.cli import main as compile_main

    status = compile_main(command)
    if status != 0:
        return status

    print(f"built {display_path(str(output))}")

    return 0


def show(argv: list[str]) -> int:
    """
    Read the selected package's manifest and print it.
    """
    args = argparse.ArgumentParser(
        prog="sie", description="Sie package manager",
        epilog="commands:\n"
               "  build [path]     compile a package into its build/\n"
               "  install [path]   copy a package into $SIE_PATH/lib\n"
               "  list             list what is installed there\n"
               "  uninstall <name> remove an installed package\n\n"
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


COMMANDS = {"build": build, "install": install, "list": listing,
            "uninstall": uninstall}


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
