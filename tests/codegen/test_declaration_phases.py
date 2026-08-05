"""Phase-boundary tests for non-callable declaration inventories."""

import pytest

from siec.codegen import CodeGenerator, codegen
from siec.codegen import aliases, callables, enums, interfaces, structs
from siec.codegen.declarations import complete_declaration_inventory
from siec.lexer import lex
from siec.parser import parse


def program(source: str):
    """Parse a source string into a program."""
    return parse(lex(source))


def test_alias_collection_does_not_expand_targets():
    """Alias cycles are syntax facts until the resolution pass consumes them."""
    tree = program("@type A = B; @type B = A;")
    gen = CodeGenerator("test")

    aliases.collect_aliases(gen, tree)

    assert gen.alias_targets == {"A": "B", "B": "A"}
    assert not gen.aliases
    assert not gen.resolved_aliases

    with pytest.raises(TypeError, match="type alias cycle"):
        aliases.resolve_aliases(gen)


def test_enum_collection_does_not_resolve_backings_or_values():
    """An invalid backing type crosses collection and fails in resolution."""
    tree = program("enum E: Missing { value }")
    gen = CodeGenerator("test")

    enums.collect_enums(gen, tree)

    assert gen.enums["E"].backing == "Missing"
    assert not gen.enums["E"].members
    assert not gen.structs
    assert not gen.resolved_enums

    with pytest.raises(TypeError, match="needs an integer backing type"):
        enums.resolve_enums(gen)


def test_extension_collection_publishes_no_claim_evidence():
    """Invalid extension meaning is untouched by raw claim collection."""
    tree = program("@extend<T: Missing> T: Nope;")
    gen = CodeGenerator("test")

    interfaces.collect_extensions(gen, tree)

    assert gen.extension_declarations == tree.extends
    assert not gen.generic_claims
    assert not gen.array_claims
    assert not gen.implements

    with pytest.raises(TypeError, match="not an interface"):
        interfaces.resolve_extensions(gen)


def test_extension_check_rejects_unresolved_claims():
    """Checking cannot consume raw extension declarations."""
    tree = program("@extend<T: Missing> T: Nope;")
    gen = CodeGenerator("test")
    interfaces.collect_extensions(gen, tree)

    with pytest.raises(RuntimeError, match="unresolved extension claims"):
        interfaces.check_extensions(gen)


@pytest.mark.parametrize(
    ("collector", "first", "later"),
    [
        (aliases.collect_aliases, "@type A = i32;", "@type B = i32;"),
        (enums.collect_enums, "enum A { value }", "enum B { value }"),
        (
            interfaces.collect_extensions,
            "@extend<T: Missing> T: First;",
            "@extend<T: Missing> T: Second;",
        ),
    ],
)
def test_frozen_inventory_rejects_late_collection(collector, first, later):
    """No non-callable declaration may enter after collection freezes."""
    tree = program(first)
    gen = CodeGenerator("test")
    collector(gen, tree)
    complete_declaration_inventory(gen, tree)

    with pytest.raises(RuntimeError, match="inventory was frozen"):
        collector(gen, program(later))


def test_pipeline_advances_each_declaration_once():
    """Every active declaration reaches its required semantic state."""
    gen = CodeGenerator("test")
    codegen(program("""
    @type Word = i32;

    enum Choice {
        yes
    }

    interface Marker {}
    @extend Word: Marker;

    fn main() -> i32 {
        return Choice::yes - 1;
    }
    """), "test", gen=gen)

    assert gen.declaration_inventory_complete
    assert gen.collected_aliases == gen.resolved_aliases
    assert gen.collected_enums == gen.resolved_enums
    assert gen.collected_extensions == gen.resolved_extensions
    assert gen.resolved_extensions == gen.checked_extensions


def test_resolution_consumes_frozen_collected_inventories():
    """Resolution can consume syntax collected without constructing IR."""
    tree = program("""
    @type Word = i32;
    enum Choice { yes }
    interface Marker {}
    struct Item {}
    @extend Item: Marker;
    fn read(value: Word) -> Word;
    """)
    gen = CodeGenerator("test")

    aliases.collect_aliases(gen, tree)
    callables.collect_callables(gen, tree)
    enums.collect_enums(gen, tree)
    structs.declare_structs(gen, tree)
    interfaces.collect_extensions(gen, tree)
    complete_declaration_inventory(gen, tree)
    callables.complete_callable_inventory(gen, tree)

    interfaces.resolve_extensions(gen)
    enums.resolve_enums(gen)
    aliases.resolve_aliases(gen)
    structs.define_structs(gen, tree)
    callables.resolve_callables(gen)

    assert gen.declaration_inventory_complete
    assert gen.callable_inventory_complete
    assert gen.aliases["Word"] == "i32"
    assert gen.enums["Choice"].members == {"yes": 0}
    assert gen.implements["Item"] == {"Marker"}
    assert "read(i32)" in gen.resolved_functions
    assert not gen.module.globals
    assert not gen.module.context.identified_types
