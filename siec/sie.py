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
import secrets
import shutil
import sys
import tomllib
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from siec.codegen.errors import display_path

MANIFEST = "package.toml"

# what a package can be: an app is built, a library installed
KINDS = ("app", "library")

# A package identity becomes a directory name and, for an app, an output
# filename. Keep it portable and unambiguous in '<name>@<version>' specs.
PACKAGE_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._+\-]*")

# A dependency requirement is one operator and a dotted numeric version.
# '*' is handled separately as the only wildcard.
REQUIREMENT = re.compile(
    r"(?P<operator>>=|<=|!=|==|>|<|=|~|\^)?\s*"
    r"(?P<version>\d+(?:\.\d+)*)")

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


def manifest_component(manifest: Path, location: str,
                       value: object) -> str:
    """
    A manifest value that is safe to use as one filename component.
    """
    if not isinstance(value, str) or not PACKAGE_COMPONENT.fullmatch(value):
        raise ValueError(f"{display_path(str(manifest))}: {location} "
                         "must be a non-empty filename component containing "
                         "only letters, digits, '.', '_', '+', or '-'")

    if "@" in value or value in (".", ".."):
        raise ValueError(f"{display_path(str(manifest))}: {location} "
                         "must not contain '@' or name '.' or '..'")

    return value


def package_component(manifest: Path, key: str, value: object) -> str:
    """A safe package identity component, retained as the public helper."""
    return manifest_component(manifest, f"[package] {key!r}", value)


def contained_path(root: Path, component: str, manifest: Path) -> Path:
    """
    Join a validated component below a root, defending the filesystem
    boundary even when an existing path is a symlink.
    """
    target = root / component
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"{display_path(str(manifest))}: package path "
                         f"{component!r} reaches outside {root}")

    return target


@dataclass(frozen=True)
class Requirement:
    """One dependency requirement, parsed once at manifest validation."""
    text: str
    operator: str
    version: tuple[int, ...]

    def __repr__(self) -> str:
        return repr(self.text)


@dataclass(frozen=True)
class PackageManifest:
    """
    The normalized, validated interpretation of one package manifest.

    Optional identity components remain optional because an app needs no
    version and a configuration-only manifest needs neither. Commands apply
    their contextual requirements through require_identity().
    """
    manifest: Path
    name: str | None
    version: str | None
    kind: str | None
    sources: tuple[str, ...]
    documents: tuple[str, ...]
    includes: tuple[str, ...]
    requirements: dict[str, Requirement]
    libraries: tuple[str, ...]
    description: str

    @property
    def path(self) -> Path:
        return self.manifest.parent

    @property
    def spec(self) -> str:
        if self.name is None:
            return self.path.name
        return f"{self.name}@{self.version}" if self.version else self.name

    def require_identity(self, *, version: bool) -> tuple[str, str | None]:
        """Require the identity components needed by the calling command."""
        missing = []
        if self.name is None:
            missing.append("name")
        if version and self.version is None:
            missing.append("version")

        if missing:
            raise ValueError(f"{display_path(str(self.manifest))}: "
                             f"[package] declares no "
                             + " or ".join(repr(key) for key in missing))

        return self.name, self.version

    def require_kind(self) -> str:
        """Require an actionable app or library declaration."""
        if self.kind is None:
            raise ValueError(
                f"{display_path(str(self.manifest))}: declares neither "
                "[app] nor [library], so there is nothing to build or install")
        return self.kind

    def source_dirs(self) -> list[Path]:
        """Existing source directories exported on the include path."""
        return [self.path / entry for entry in self.sources
                if (self.path / entry).is_dir()]

    def source_files(self) -> list[Path]:
        """Compilation units named directly by the normalized sources."""
        files = []
        for entry in self.sources:
            path = self.path / entry
            if path.is_file():
                files.append(path)
            elif path.is_dir():
                files.extend(sorted(path.glob("*.sie")))

        return files

    def libs(self) -> tuple[str, ...]:
        return self.libraries

    def dependencies(self) -> dict[str, Requirement]:
        return self.requirements

    def content_entries(self) -> list[str]:
        """The unique paths copied by installation, in manifest order."""
        return list(dict.fromkeys((MANIFEST, *self.documents, *self.sources)))


def manifest_table(manifest: Path, data: dict, name: str,
                   *, optional: bool = True) -> dict:
    """A TOML table with a consistent diagnostic when its type is wrong."""
    value = data.get(name)
    if value is None and optional:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{display_path(str(manifest))}: [{name}] "
                         "must be a table")
    return value


def manifest_list(manifest: Path, section: str, key: str,
                  value: object) -> tuple[str, ...]:
    """A string-or-string-array manifest field, normalized and deduplicated."""
    if value is None:
        return ()
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        values = value
    else:
        raise ValueError(f"{display_path(str(manifest))}: [{section}] "
                         f"{key!r} must be a string or a list of strings")

    if any(not item for item in values):
        raise ValueError(f"{display_path(str(manifest))}: [{section}] "
                         f"{key!r} entries must not be empty")

    return tuple(dict.fromkeys(values))


def manifest_paths(manifest: Path, section: str, key: str,
                   value: object) -> tuple[str, ...]:
    """Normalized package-relative paths from a manifest list field."""
    normalized = []
    for entry in manifest_list(manifest, section, key, value):
        path = Path(entry)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"{display_path(str(manifest))}: "
                             f"{entry!r} reaches outside the package")

        normalized.append(path.as_posix().rstrip("/") or ".")

    return tuple(dict.fromkeys(normalized))


def validate_manifest(manifest: Path, data: dict) -> PackageManifest:
    """Validate raw TOML once and return its normalized package model."""
    package = manifest_table(manifest, data, "package")
    kinds = []
    for kind in KINDS:
        if kind not in data:
            continue
        table = manifest_table(manifest, data, kind, optional=False)
        kinds.append((kind, table))

    if len(kinds) > 1:
        raise ValueError(f"{display_path(str(manifest))}: declares both "
                         "[app] and [library]; a package is one or the other")

    kind, made_of = kinds[0] if kinds else (None, {})

    name = package.get("name")
    if name is not None:
        name = package_component(manifest, "name", name)

    version = package.get("version")
    if version is not None:
        version = package_component(manifest, "version", version)

    description = package.get("description")
    if description is None:
        description = ""
    elif not isinstance(description, str):
        raise ValueError(f"{display_path(str(manifest))}: [package] "
                         "'description' must be a string")

    sources = manifest_paths(
        manifest, kind or "package", "sources", made_of.get("sources"))
    readme = manifest_paths(
        manifest, "package", "readme", package.get("readme"))
    licenses = manifest_paths(
        manifest, "package", "license-files",
        package.get("license-files"))
    includes = manifest_paths(
        manifest, "package", "include", package.get("include"))
    libraries = manifest_list(
        manifest, kind or "package", "libs", made_of.get("libs"))

    dependencies = manifest_table(manifest, data, "dependencies")
    requirements = {}
    for dependency, requirement in dependencies.items():
        dependency = manifest_component(
            manifest, f"dependency name {dependency!r}", dependency)
        if not isinstance(requirement, str):
            raise ValueError(f"{display_path(str(manifest))}: dependency "
                             f"{dependency!r} requirement must be a string")
        try:
            operator, wanted = parse_requirement(requirement)
        except ValueError as error:
            raise ValueError(f"{display_path(str(manifest))}: dependency "
                             f"{dependency!r}: {error}") from error
        requirements[dependency] = Requirement(requirement, operator, wanted)

    return PackageManifest(
        manifest.resolve(),
        name,
        version,
        kind,
        sources,
        tuple(dict.fromkeys((*readme, *licenses))),
        includes,
        requirements,
        libraries,
        description,
    )


def load_package(manifest: Path) -> PackageManifest:
    """Read and validate a package manifest through the shared boundary."""
    return validate_manifest(manifest, read_manifest(manifest))


def install_entries(package: PackageManifest) -> list[str]:
    """
    Validate the physical content selected by a package and return what
    installation copies.
    """
    entries = package.content_entries()
    for entry in entries:
        validate_content_path(package.path, package.path / entry)

    return entries


def identity(manifest: Path, data: dict) -> tuple[str, str]:
    """Compatibility wrapper around centralized manifest validation."""
    package = validate_manifest(manifest, data)
    name, version = package.require_identity(version=True)
    return name, version


def unit(manifest: Path, data: dict) -> tuple[str, dict]:
    """Compatibility wrapper returning the selected raw kind table."""
    package = validate_manifest(manifest, data)
    kind = package.require_kind()
    return kind, data[kind]


def contents(package: Path, data: dict) -> list[str]:
    """Compatibility wrapper returning validated install entries."""
    model = validate_manifest(package / MANIFEST, data)
    model.require_kind()
    return install_entries(model)


def validate_content_path(package: Path, path: Path) -> None:
    """
    Ensure a declared path and every path below it stay in the package.

    Internal symlinks are allowed and copied as their contents. Broken
    links, cycles, and links resolving outside the package are rejected
    before a staging directory is created.
    """
    try:
        root = package.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{display_path(str(package))}: "
                         f"{error.strerror}") from error

    active: set[Path] = set()

    def walk(current: Path) -> None:
        try:
            current.lstat()
        except FileNotFoundError:
            # Missing ordinary entries retain copy_into()'s warning-and-skip
            # behavior. A broken symlink has an lstat entry and is rejected
            # when it is resolved below.
            return
        except OSError as error:
            raise ValueError(
                f"{display_path(str(current))}: {error.strerror}") from error

        try:
            resolved = current.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            detail = getattr(error, "strerror", None) or str(error)
            raise ValueError(f"{display_path(str(current))}: invalid package "
                             f"symlink: {detail}") from error

        if not resolved.is_relative_to(root):
            raise ValueError(f"{display_path(str(current))}: symlink reaches "
                             "outside the package")

        if not resolved.is_dir():
            return

        if resolved in active:
            raise ValueError(f"{display_path(str(current))}: package symlink "
                             "forms a directory cycle")

        active.add(resolved)
        try:
            for child in resolved.iterdir():
                walk(child)
        except OSError as error:
            raise ValueError(
                f"{display_path(str(current))}: {error.strerror}") from error
        finally:
            active.remove(resolved)

    walk(path)


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
    previous = None
    if target.exists():
        while previous is None:
            candidate = target.with_name(
                f"{target.name}.backup-{secrets.token_hex(16)}")
            if not candidate.exists() and not candidate.is_symlink():
                previous = candidate

        target.rename(previous)

    try:
        staging.rename(target)
    except OSError:
        if previous is not None and previous.exists():
            previous.rename(target)
        raise

    if previous is not None:
        shutil.rmtree(previous, ignore_errors=True)


def install_details(manifest: Path) -> tuple[PackageManifest, list[str]]:
    """
    Validate a library package and return everything installation needs.

    This runs both on the source and on the completed staging copy, so the
    installed version is never moved aside for content that changed or became
    invalid while it was being copied.
    """
    package = load_package(manifest)
    if package.require_kind() != "library":
        raise ValueError(f"{display_path(str(manifest))}: an [app] is "
                         "built, not installed")

    package.require_identity(version=True)
    return package, install_entries(package)


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
        model, entries = install_details(manifest)
        name, version = model.require_identity(version=True)
        target = contained_path(install_root(), f"{name}@{version}", manifest)
    except ValueError as error:
        print(f"sie: {error}", file=sys.stderr)
        return 1

    if not model.sources:
        print(f"sie: warning: {display_path(str(manifest))} declares no "
              "[library] 'sources', so only its manifest is installed",
              file=sys.stderr)

    staging = target.with_name("." + target.name + ".partial")

    try:
        # Manifest and path validation have completed above. Build the new
        # install in full before replace() moves the currently installed one.
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()

        copied = copy_into(package, entries, staging)

        # Validate what was actually copied, not only the source snapshot
        # read above. A concurrently changed or corrupted manifest never
        # reaches replace(), so the installed version remains in place.
        staged, _ = install_details(staging / MANIFEST)
        if (staged.name, staged.version) != (name, version):
            raise ValueError(
                f"{display_path(str(staging / MANIFEST))}: package identity "
                "changed while staging")

        replaced = target.exists()
        replace(staging, target)
    except ValueError as error:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"sie: {error}", file=sys.stderr)
        return 1
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


def parse_requirement(requirement: str) -> tuple[str, tuple[int, ...]]:
    """
    Parse one dependency requirement.

    Raises ValueError rather than allowing a misspelling to become an empty
    numeric prefix that happens to match every installed version.
    """
    requirement = requirement.strip()
    if requirement == "*":
        return "*", ()

    match = REQUIREMENT.fullmatch(requirement)
    if match is None:
        raise ValueError(f"invalid version requirement {requirement!r}; "
                         "expected '*', a dotted numeric version, or one "
                         "preceded by ~, ^, or a comparison operator")

    parts = tuple(int(piece) for piece in match["version"].split("."))
    return match["operator"] or "", parts


def satisfies(version: str, requirement: str | Requirement) -> bool:
    """
    Whether an installed version answers a dependency's requirement.

    '*' takes anything, '~1' and '~1.2' allow later releases that keep the
    parts written down, '^1.2' allows anything up to the next version that
    may break, a comparison compares, and a bare version is that version.
    """
    if isinstance(requirement, Requirement):
        operator, wanted = requirement.operator, requirement.version
    else:
        operator, wanted = parse_requirement(requirement)
    if operator == "*":
        return True

    found = version_parts(version)

    if operator in (">=", "<=", "!=", "==", ">", "<", "="):
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

    if operator in ("~", "^"):
        found = version_parts(version)

        # a tilde pins the major and the minor, or the major alone when
        # that is all it names; a caret pins up to the first part that is
        # not zero: both mean 'newer, but not different'
        if operator == "~":
            pinned = min(len(wanted), 2)
        else:
            pinned = next((i + 1 for i, part in enumerate(wanted) if part), len(wanted))

        return found[:pinned] == wanted[:pinned] and found >= wanted

    return found[:len(wanted)] == wanted


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
        model = load_package(package / MANIFEST)
        if model.require_kind() != "library":
            return ""
        identity = model.require_identity(version=True)
        name, separator, version = package.name.partition("@")
        if not separator or identity != (name, version):
            return ""
    except ValueError:
        return ""

    return model.description


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


def resolve(root: PackageManifest) -> list[PackageManifest]:
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

    loaded: dict[Path, PackageManifest] = {}

    def package(name: str, version: str, path: Path) -> PackageManifest:
        if path in loaded:
            return loaded[path]

        try:
            model = load_package(path / MANIFEST)
            if model.kind != "library":
                raise ValueError(
                    f"{display_path(str(model.manifest))}: an installed "
                    "dependency must declare [library]")
            found_name, found_version = model.require_identity(version=True)
            if (found_name, found_version) != (name, version):
                raise ValueError(
                    f"{display_path(str(model.manifest))}: package identity "
                    f"{model.spec!r} does not match installed directory "
                    f"{name}@{version!s}")
        except ValueError as error:
            raise LookupError(str(error)) from error

        loaded[path] = model
        return loaded[path]

    def live_graph(chosen: dict[str, PackageManifest]):
        """
        Requirements and discovery order reachable through the versions
        currently chosen. Dependencies of an abandoned version disappear
        because that package is no longer traversed.
        """
        requirements: dict[str, list[tuple[Requirement, str]]] = {}
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

    def search(chosen: dict[str, PackageManifest]
               ) -> list[PackageManifest] | None:
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

    try:
        resolved = search({})
    except ValueError as error:
        raise LookupError(str(error)) from error

    if resolved is not None:
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
        root = load_package(manifest)

        # a library has no entry point: it is installed, and an app is
        # what turns it into something that runs
        if root.require_kind() != "app":
            raise ValueError(f"{display_path(str(manifest))}: a [library] is "
                             "installed, not built")
        name, _ = root.require_identity(version=False)
    except (FileNotFoundError, ValueError) as error:
        print(f"sie: {error}", file=sys.stderr)
        return 1

    package = manifest.parent

    try:
        output = contained_path(package / "build", name, manifest)
    except ValueError as error:
        print(f"sie: {error}", file=sys.stderr)
        return 1

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
