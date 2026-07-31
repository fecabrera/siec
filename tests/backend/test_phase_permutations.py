"""Systematic declaration-order and namespace-invariance matrices."""

from itertools import combinations, permutations

import pytest

from siec.codegen import CodeGenerator, codegen
from siec.lexer import lex
from siec.parser import parse


def compile_state(source: str) -> tuple:
    """Compile source and return an order-insensitive semantic fingerprint."""
    gen = CodeGenerator("permutation")
    codegen(parse(lex(source)), "permutation", gen=gen)
    return (
        tuple(sorted(gen.aliases.items())),
        tuple(sorted(
            (name, info.backing, tuple(sorted(info.members.items())))
            for name, info in gen.enums.items()
        )),
        tuple(sorted(
            (name, tuple((field.name, field.type) for field in info.fields or ()))
            for name, info in gen.structs.items()
            if not name.startswith(("Result<", "Tuple<"))
        )),
        tuple(sorted(gen.globals.items())),
        tuple(sorted(
            (name, tuple(sorted(claims)))
            for name, claims in gen.implements.items()
        )),
        tuple(sorted(gen.return_types.items())),
        tuple(sorted(
            (name, tuple(params))
            for name, params in gen.param_types.items()
        )),
    )


CASES = [
    (
        "aliases",
        ("@type Word = Wide;", "@type Wide = i32;"),
        "fn main() -> i32 { let value: Word = 42; return value; }",
    ),
    (
        "enums",
        (
            "enum Answer { value = Numbers::answer }",
            "enum Numbers { answer = 42 }",
        ),
        "fn main() -> i32 { return Answer::value as i32; }",
    ),
    (
        "structs",
        ("struct Outer { inner: Inner; }", "struct Inner { value: i32; }"),
        "fn main() -> i32 { let value: Outer = {{42}}; "
        "return value.inner.value; }",
    ),
    (
        "interfaces",
        (
            "interface Value { fn value(const &self) -> i32; }",
            "struct Item {}",
            "@extend Item: Value;",
            "fn Item::value(const &self) -> i32 { return 42; }",
        ),
        "fn main() -> i32 { let item: Item = {}; return item.value(); }",
    ),
    (
        "bounded generics",
        (
            "interface Marker {}",
            "struct Item {}",
            "@extend Item: Marker;",
            "struct Box<T: Marker> { value: T; }",
        ),
        "fn main() -> i32 { let box: Box<Item> = {{}}; return 42; }",
    ),
    (
        "callables",
        (
            "fn T[]::score(const &self) -> i32 { return 1; }",
            "@override fn char[]::score(const &self) -> i32 { return 42; }",
            "fn identity<T>(value: T) -> T { return value; }",
        ),
        "fn main() -> i32 { return identity(\"value\".score()); }",
    ),
    (
        "constants and globals",
        (
            "@const BASE = LATER + 1;",
            "@const LATER = 40;",
            "@static let answer: i32 = BASE + 1;",
        ),
        "fn main() -> i32 { return answer; }",
    ),
]


@pytest.mark.parametrize(("family", "blocks", "tail"), CASES)
def test_declaration_permutation_matrix(family, blocks, tail):
    """Every declaration family has one meaning under every source order."""
    fingerprints = {
        compile_state("\n".join((*ordered, tail)))
        for ordered in permutations(blocks)
    }
    assert len(fingerprints) == 1, family


def test_invalid_permutations_keep_the_same_diagnostic():
    """Collector order does not choose a cross-category collision error."""
    blocks = ("@type Shared = i32;", "struct Shared { value: i32; }")
    messages = set()
    for ordered in permutations(blocks):
        with pytest.raises(TypeError) as info:
            compile_state("\n".join(ordered))
        messages.add(str(info.value))

    assert messages == {"type 'Shared' is declared more than once"}


TYPE_DECLARATIONS = {
    "alias": "@type Shared = i32;",
    "enum": "enum Shared { value }",
    "struct": "struct Shared { value: i32; }",
    "generic struct": "struct Shared<T> { value: T; }",
    "interface": "interface Shared {}",
}


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pair
        for categories in combinations(TYPE_DECLARATIONS, 2)
        for pair in (categories, categories[::-1])
    ],
)
def test_type_namespace_collision_matrix(compile_source, left, right):
    """Every pair of global type categories shares one collision rule."""
    source = TYPE_DECLARATIONS[left] + TYPE_DECLARATIONS[right]
    with pytest.raises(TypeError, match="type 'Shared' is declared more than once"):
        compile_source(source)


@pytest.mark.parametrize("builtin", ("i32", "opaque", "Tuple"))
@pytest.mark.parametrize("declaration", TYPE_DECLARATIONS.values())
def test_global_type_categories_cannot_shadow_builtins(
        compile_source, declaration, builtin):
    """Builtin names belong to the same global type namespace."""
    source = declaration.replace("Shared", builtin)
    with pytest.raises(TypeError, match="builtin type|declared more than once"):
        compile_source(source)


@pytest.mark.parametrize(
    ("declaration", "parameter"),
    [
        *((declaration, "Shared")
          for declaration in TYPE_DECLARATIONS.values()),
        ("", "i32"),
        ("", "opaque"),
        ("", "Tuple"),
    ],
)
def test_lexical_type_parameter_shadows_every_global_category(
        compile_source, declaration, parameter):
    """A lexical parameter wins over global and builtin type identities."""
    source = f"""
    {declaration}
    fn identity<{parameter}>(value: {parameter}) -> {parameter} {{
        return value;
    }}
    fn main() -> i32 {{ return identity<u8>(42 as u8) as i32; }}
    """
    compile_source(source)


@pytest.mark.parametrize(
    ("prefix", "declaration", "message"),
    [
        ("", "@type Duplicate = i32;", "declared more than once"),
        ("", "enum Duplicate { value }", "declared more than once"),
        ("", "struct Duplicate { value: i32; }", "declared more than once"),
        ("", "struct Duplicate<T> { value: T; }", "declared more than once"),
        ("", "interface Duplicate {}", "declared more than once"),
        ("", "@const Duplicate = 1;", "declared more than once"),
        ("", "@static let Duplicate: i32 = 1;", "declared more than once"),
        ("", "fn duplicate() -> i32 { return 1; }", "more than once"),
        (
            "struct Receiver {}",
            "fn Receiver::duplicate(const &self) -> i32 { return 1; }",
            "more than once",
        ),
    ],
)
def test_direct_and_selected_declarations_share_duplicate_rules(
        compile_source, prefix, declaration, message):
    """A selected declaration behaves exactly like a direct declaration."""
    source = f"""
    {prefix}
    {declaration}
    @if (true) {{
        {declaration}
    }}
    """
    with pytest.raises(TypeError, match=message):
        compile_source(source)
