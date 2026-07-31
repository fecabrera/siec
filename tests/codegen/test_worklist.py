"""Tests for the semantic worklist behind lazy function instances."""

import pytest

from siec.codegen import CodeGenerator, codegen
from siec.codegen import interfaces, methods
from siec.lexer import lex
from siec.parser import parse


def compile_with_state(source: str) -> CodeGenerator:
    """Compile source while retaining the generator's semantic state."""
    gen = CodeGenerator("test")
    codegen(parse(lex(source)), "test", gen=gen)
    return gen


def test_conformance_resolves_receiver_families_before_checking(
        monkeypatch):
    """
    A generic method required by a late generic-struct claim specializes
    during claim resolution. The check only looks up that resolved method.
    """
    events = []
    original_resolve = methods.resolve_method
    original_check = interfaces.check_conformance

    def record_resolve(gen, receiver, method, *, specialize=True):
        if receiver == "Box<i32>" and method == "value":
            events.append("resolve" if specialize else "lookup")
        return original_resolve(
            gen,
            receiver,
            method,
            specialize=specialize,
        )

    def record_check(gen, name, *args):
        if name == "Box<i32>":
            events.append("check")
        return original_check(gen, name, *args)

    monkeypatch.setattr(methods, "resolve_method", record_resolve)
    monkeypatch.setattr(interfaces, "check_conformance", record_check)

    gen = compile_with_state("""
    interface Value {
        fn value(const &self) -> i32;
    }

    struct Box<T>: Value {
        value: T;
    }

    fn Box<T>::value(const &self) -> i32 {
        return 42;
    }

    fn main() -> i32 {
        let box: Box<i32> = { 0 };
        return 0;
    }
    """)

    assert events == ["resolve", "check", "lookup"]
    assert {
        symbol: state
        for symbol, state in gen.function_instance_states.items()
        if symbol.startswith("Box<i32>::value")
    } == {"Box<i32>::value(&Box<i32>)": "checked"}


def test_function_instance_worklist_reaches_a_fixed_point():
    """
    A checked instance may request another one. Both headers resolve and
    both bodies finish checking before compilation returns.
    """
    gen = compile_with_state("""
    fn leaf<T>(value: T) -> T {
        return value;
    }

    fn root<T>(value: T) -> T {
        return leaf(value);
    }

    fn main() -> i32 {
        return root(42);
    }
    """)

    assert gen.function_instance_states == {
        "root<i32>": "checked",
        "leaf<i32>": "checked",
    }
    assert not gen.pending_functions
    assert not gen.pending_conformance
    assert not gen.resolved_conformance


def test_conformance_check_rejects_unresolved_claims():
    """The check boundary cannot silently consume a raw interface claim."""
    gen = CodeGenerator("test")
    gen.pending_conformance.append(("S", "S", ["I"], 1, "<test>"))

    with pytest.raises(
            RuntimeError,
            match="cannot check unresolved interface claims"):
        interfaces.run_conformance(gen)


def test_conformance_resolution_carries_no_active_function(monkeypatch):
    """
    A claim discovered while checking a body resolves between bodies. No
    function is active then, so a specialization the claim requests cannot
    record the previously checked body as its instantiation site.
    """
    observed = []
    original = interfaces.resolve_conformance

    def record(gen):
        if gen.pending_conformance:
            observed.append(gen.current_function)
        return original(gen)

    monkeypatch.setattr(interfaces, "resolve_conformance", record)

    compile_with_state("""
    interface Value {
        fn value(const &self) -> i32;
    }

    struct Box<T>: Value {
        value: T;
    }

    fn Box<T>::value(const &self) -> i32 {
        return 42;
    }

    fn main() -> i32 {
        let box: Box<i32> = { 0 };
        return 0;
    }
    """)

    assert observed
    assert set(observed) == {None}
