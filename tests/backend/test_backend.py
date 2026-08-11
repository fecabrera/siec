"""Tests for siec.backend: native object emission, linking, and JIT isolation.

Feature behavior is covered by the per-feature files in this directory; these
tests exercise the backend mechanism itself.
"""

import os
import signal
import sys

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


@pytest.mark.skipif(sys.platform == "win32", reason="uses the POSIX fork worker")
def test_jit_worker_contains_an_abrupt_native_termination(
        monkeypatch, compile_source):
    """A signal that kills native execution cannot terminate the compiler."""
    import siec.backend as backend

    def terminate(*args):
        os.kill(os.getpid(), signal.SIGKILL)

    monkeypatch.setattr(backend, "_run_jit_in_process", terminate)
    with pytest.raises(OSError, match="JIT worker terminated by"):
        backend.run_jit(compile_source(SOURCE), ["test.sie"])
