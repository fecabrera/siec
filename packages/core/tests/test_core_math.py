"""Runtime tests for std.math integer helpers."""

import subprocess
import sys
from pathlib import Path

from siec.cli import main


ROOT = Path(__file__).parents[3]
CORE_INCLUDES = (
    ROOT / "packages/libc/src",
    ROOT / "packages/posix/src",
    ROOT / "packages/core/src",
)


def run_cli(monkeypatch, *argv):
    """Invoke the compiler command without depending on its test helpers."""
    monkeypatch.setattr(sys, "argv", ["siec", *map(str, argv)])
    return main()


def run_core(monkeypatch, tmp_path, source: str):
    """Compile and run one program against the workspace core sources."""
    path = tmp_path / "main.sie"
    executable = tmp_path / "main"
    path.write_text(source)

    args = [path]
    for include in CORE_INCLUDES:
        args.extend(("-I", include))
    args.extend(("-o", executable))

    assert run_cli(monkeypatch, *args) == 0
    return subprocess.run([str(executable)], capture_output=True, text=True)


def test_signed_to_unsigned_reinterprets_same_width_bits(monkeypatch, tmp_path):
    """Each signed width maps to its unsigned counterpart of equal size."""
    result = run_core(monkeypatch, tmp_path, """
    import std.math;

    fn main() -> i32 {
        let a: i8 = -1;
        let b: i16 = -1;
        let c: i32 = -1;
        let d: i64 = -1;
        let e: i128 = -1;
        if (a.to_unsigned() != 255 as u8) { return 1; }
        if (b.to_unsigned() != 65535 as u16) { return 2; }
        if (c.to_unsigned() != 4294967295 as u32) { return 3; }
        if (d.to_unsigned() != 18446744073709551615 as u64) { return 4; }
        if (e.to_unsigned() != (~0 as u128)) { return 5; }
        return 42;
    }
    """)
    assert result.returncode == 42


def test_abs_overloads_cover_signed_and_unsigned(monkeypatch, tmp_path):
    """Signed and unsigned Integer abs share a name under disjoint bounds."""
    result = run_core(monkeypatch, tmp_path, """
    import std.math;

    fn main() -> i32 {
        let neg: i32 = -40;
        let pos: u32 = 2;
        return neg.abs() + pos.abs() as i32;
    }
    """)
    assert result.returncode == 42
