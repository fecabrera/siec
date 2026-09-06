"""Core containers and formatting preserve const ownership contracts."""

from pathlib import Path

from tests.cli.test_cli import run_cli


def test_core_const_owners_and_borrowed_access(tmp_path, monkeypatch):
    """List access clones owners while formatting borrows erased payloads."""
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "main.sie"
    source.write_text(r"""
    import { List, String } from std.collections;
    import { format, Formattable } from std.format;

    @static let drops: i32 = 0;
    @static let clones: i32 = 0;

    struct Resource: Destroy, Clone, Formattable { id: i32; }
    fn Resource::destroy(&self) { drops += 1; }
    fn Resource::clone(const &self) -> Resource {
        clones += 1;
        let result: Resource = {self.id};
        return result;
    }
    fn Resource::format(const &self, modifiers: const &char[]) -> String {
        return format("{}", self.id);
    }
    fn exercise() -> i32 {
        let values = List<Resource>();
        let original: Resource = {42};
        values.push(original);
        let copy: const Resource = values[0];
        values += copy;
        let text: const String = format("{}", values[0]);
        if (drops != 0 or clones != 2) return 1;
        if (text.get_length() != 2) return 2;
        drop values;
        if (drops != 2 or copy.id != 42) return 3;
        return 0;
    }
    fn main() -> i32 {
        let status = exercise();
        if (status != 0) return status;
        if (drops != 3) return 4;
        return 0;
    }
    """)
    includes = [
        argument
        for package in ("core", "libc", "posix")
        for argument in ("-I", root / "packages" / package / "src")
    ]
    assert run_cli(monkeypatch, source, *includes,
                   "-o", tmp_path / "program", "--run") == 0
