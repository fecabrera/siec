"""Tests for siec.backend: native object emission and linking.

Feature behavior is covered by the per-feature files in this directory; these
tests exercise the backend mechanism itself.
"""

import pytest

from siec.backend import TargetError, compile_to_object, emit_llvm, link

SOURCE = "fn main() -> i32 { return 7; }"


def test_compile_to_object_writes_native_code(tmp_path, compile_source):
    """
    Object emission writes a non-empty file to the given path.
    """
    obj = tmp_path / "m.o"
    compile_to_object(compile_source(SOURCE), str(obj))
    assert obj.stat().st_size > 0


def test_compile_to_object_sets_the_host_triple(tmp_path, compile_source):
    """
    The module is retargeted to the host before emission.
    """
    module = compile_source(SOURCE)
    compile_to_object(module, str(tmp_path / "m.o"))
    assert module.triple != "unknown-unknown-unknown"


def test_emit_llvm_validates_an_explicit_target_at_o0(compile_source):
    """
    Raw -O0 IR still rejects a target for which LLVM cannot generate code.
    """
    with pytest.raises(TargetError, match="cannot use target"):
        emit_llvm(compile_source(SOURCE), target="definitely-not-a-target")


def test_link_produces_a_runnable_executable(run):
    """
    Linking yields an executable that returns the program's exit code.
    """
    assert run(SOURCE).returncode == 7
