"""Tests for the raw callable declaration inventory."""

import pytest

from siec.codegen import CodeGenerator, codegen
from siec.codegen import callables, globals as global_declarations, structs
from siec.lexer import lex
from siec.parser import parse


def program(source: str):
    """Parse a source string into a program."""
    return parse(lex(source))


def test_collection_records_raw_headers_without_resolving_them():
    """
    Collection owns written callable identities but does not ask what their
    annotated types mean.
    """
    tree = program("fn f(value: Missing) -> Missing;")
    gen = CodeGenerator("test")

    callables.collect_callables(gen, tree)

    assert gen.callable_declarations == tree.functions
    assert gen.raw_callables == {"f": tree.functions}
    assert tree.functions[0].params[0].type == "Missing"
    assert tree.functions[0].return_type == "Missing"
    assert not gen.resolved_functions
    assert not gen.generic_functions


def test_callables_are_collected_before_layouts_and_globals(monkeypatch):
    """
    Struct-field and global resolution see the raw callable inventory, while
    no callable header has resolved yet.
    """
    original_define = structs.define_structs
    original_globals = global_declarations.register_globals
    events = []

    def inspect_layout_boundary(gen, tree):
        if any(fn.name == "later" for fn in tree.functions):
            events.append("layouts")
            assert "later" in gen.raw_callables
            assert not gen.resolved_functions
            assert not gen.generic_functions
        original_define(gen, tree)

    def inspect_global_boundary(gen, tree):
        events.append("globals")
        assert gen.callable_inventory_complete
        assert {"later", "generic", "main"} <= gen.raw_callables.keys()
        assert not gen.callables_resolved
        original_globals(gen, tree)

    monkeypatch.setattr(structs, "define_structs", inspect_layout_boundary)
    monkeypatch.setattr(
        global_declarations,
        "register_globals",
        inspect_global_boundary,
    )

    gen = CodeGenerator("test")
    codegen(program("""
    fn later(value: S) -> i32 {
        return value.number;
    }

    fn generic<T>(value: T) -> T {
        return value;
    }

    struct S {
        number: i32;
    }

    @static let stored: i32 = 1;

    fn main() -> i32 {
        return later({ stored });
    }
    """), "test", gen=gen)

    assert events == ["layouts", "globals"]
    assert gen.callables_resolved


def test_deferred_conditional_callables_join_the_inventory():
    """
    A type-dependent conditional contributes its chosen callables before the
    raw inventory freezes and header resolution starts.
    """
    gen = CodeGenerator("test")
    codegen(program("""
    struct Word {
        value: i32;
    }

    @if (@sizeof(Word) == 4) {
        fn selected() -> i32 {
            return 7;
        }
    }

    fn main() -> i32 {
        return selected();
    }
    """), "test", gen=gen)

    assert "selected" in gen.raw_callables
    assert gen.callable_inventory_complete
    assert gen.callables_resolved
    assert "selected()" in gen.resolved_functions


def test_frozen_callable_inventory_rejects_late_collection():
    """No declaration may enter collection after its output is frozen."""
    first = program("fn first();")
    later = program("fn later();")
    gen = CodeGenerator("test")

    callables.collect_callables(gen, first)
    callables.complete_callable_inventory(gen, first)

    with pytest.raises(RuntimeError, match="inventory was frozen"):
        callables.collect_callables(gen, later)


def test_callable_resolution_rejects_an_open_inventory():
    """Header resolution cannot begin while collection can still mutate."""
    tree = program("fn f();")
    gen = CodeGenerator("test")
    callables.collect_callables(gen, tree)

    with pytest.raises(RuntimeError, match="complete declaration inventory"):
        callables.resolve_callables(gen)
