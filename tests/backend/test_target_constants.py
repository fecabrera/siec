"""Feature tests for the compiler-defined target constants."""

import platform
import sys

import pytest

from siec.codegen.constants import target_arch, target_os

# what the compiler should report for the machine running the tests
HOST_OS = {"darwin": 1, "linux": 2, "win32": 3}.get(sys.platform, 0)
HOST_ARCH = {"x86_64": 1, "amd64": 1, "arm64": 2, "aarch64": 2,
             "riscv64": 3}.get(platform.machine().lower(), 0)


def test_target_constants_match_the_host(run):
    """
    'TARGET_OS' and 'TARGET_ARCH' hold the compilation target's families.
    """
    result = run("""
        fn main() -> i32 {
            return TARGET_OS * 10 + TARGET_ARCH;
        }
    """)
    assert result.returncode == HOST_OS * 10 + HOST_ARCH


def test_family_constants_are_defined(run):
    """
    Every OS and architecture family is a distinct named constant.
    """
    result = run("""
        fn main() -> i32 {
            return OS_UNKNOWN + OS_DARWIN + OS_LINUX + OS_WINDOWS + OS_NONE
                 + ARCH_UNKNOWN + ARCH_X86_64 + ARCH_AARCH64 + ARCH_RISCV64;
        }
    """)
    assert result.returncode == (0 + 1 + 2 + 3 + 4) + (0 + 1 + 2 + 3)


def test_target_constants_work_in_constant_contexts(run):
    """
    The target constants are ordinary '@const's: usable in other
    constants, case arms, and array sizes.
    """
    result = run("""
        @const PADDED = TARGET_ARCH + 3;

        fn main() -> i32 {
            let arr: u8[PADDED];
            case (TARGET_OS) {
                when OS_DARWIN, OS_LINUX, OS_WINDOWS:
                    return arr.length as i32;
                else:
                    return 0;
            }
        }
    """)
    expected = HOST_ARCH + 3 if HOST_OS in (1, 2, 3) else 0
    assert result.returncode == expected


def test_redefining_a_builtin_constant_is_an_error(compile_source):
    """
    The compiler's constants cannot be redeclared.
    """
    with pytest.raises(TypeError, match="'TARGET_OS' is defined by the compiler"):
        compile_source("@const TARGET_OS = 5;")

    with pytest.raises(TypeError, match="'OS_DARWIN' is defined by the compiler"):
        compile_source("@const OS_DARWIN = 9;")


@pytest.mark.parametrize("triple,os", [
    ("arm64-apple-darwin27.0.0", "OS_DARWIN"),
    ("x86_64-apple-macosx14.0.0", "OS_DARWIN"),
    ("x86_64-unknown-linux-gnu", "OS_LINUX"),
    ("x86_64-pc-windows-msvc", "OS_WINDOWS"),
    ("riscv64-unknown-none-elf", "OS_NONE"),
    ("wasm32-unknown-emscripten", "OS_UNKNOWN"),
])
def test_triple_os_classification(triple, os):
    """
    The OS comes from the triple's later components.
    """
    assert target_os(triple) == os


@pytest.mark.parametrize("triple,arch", [
    ("x86_64-unknown-linux-gnu", "ARCH_X86_64"),
    ("amd64-pc-windows-msvc", "ARCH_X86_64"),
    ("arm64-apple-darwin27.0.0", "ARCH_AARCH64"),
    ("aarch64-unknown-linux-gnu", "ARCH_AARCH64"),
    ("riscv64-unknown-none-elf", "ARCH_RISCV64"),
    ("wasm32-unknown-emscripten", "ARCH_UNKNOWN"),
])
def test_triple_arch_classification(triple, arch):
    """
    The architecture comes from the triple's first component.
    """
    assert target_arch(triple) == arch
