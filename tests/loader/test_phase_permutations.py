"""Cross-file declaration-order invariance for discovered source graphs."""

from itertools import permutations

import pytest

from siec.codegen import CodeGenerator, codegen
from siec.loader import discover_program


CROSS_FILE_CASES = [
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
            "@static let answer: i32 = BASE + 1; "
            "fn read_answer() -> i32 { return answer; }",
        ),
        "fn main() -> i32 { return read_answer(); }",
    ),
]


def write(path, text):
    """Write one test source and return its path."""
    path.write_text(text)
    return path


def compile_files(sources, include_paths=()) -> tuple:
    """Discover and compile files, returning their semantic type inventory."""
    program = discover_program(list(sources), list(include_paths))
    gen = CodeGenerator("files")
    codegen(program, "files", gen=gen)
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


@pytest.mark.parametrize(("family", "blocks", "tail"), CROSS_FILE_CASES)
def test_command_line_source_order_permutation(
        tmp_path, family, blocks, tail):
    """Entry-source order cannot change a declaration family's meaning."""
    sources = tuple(
        write(tmp_path / f"source_{index}.sie", block)
        for index, block in enumerate(blocks)
    )
    root = write(tmp_path / "main.sie", tail)

    states = {
        compile_files((*order, root))
        for order in permutations(sources)
    }
    assert len(states) == 1, family


@pytest.mark.parametrize(("family", "blocks", "tail"), CROSS_FILE_CASES)
def test_include_order_permutation(tmp_path, family, blocks, tail):
    """Textual include order cannot change a declaration family's meaning."""
    names = tuple(f"source_{index}" for index in range(len(blocks)))
    for name, block in zip(names, blocks):
        write(tmp_path / f"{name}.sie", block)

    states = set()
    for order in permutations(names):
        includes = "\n".join(f'@include("{name}");' for name in order)
        root = write(tmp_path / "main.sie", f"{includes}\n{tail}")
        states.add(compile_files((root,)))

    assert len(states) == 1, family


def test_import_order_permutation(tmp_path):
    """Module import order cannot change qualified cross-module types."""
    write(tmp_path / "b.sie", "struct B { number: i32; }")
    write(tmp_path / "a.sie", """
        import b;
        struct A { value: b.B; }
    """)

    states = set()
    for order in permutations(("a", "b")):
        root = write(tmp_path / "main.sie", f"""
            import {order[0]};
            import {order[1]};
            fn main() -> i32 {{
                let value: a.A = {{{{42}}}};
                return value.value.number;
            }}
        """)
        states.add(compile_files((root,), (tmp_path,)))

    assert len(states) == 1


def test_selected_conditional_cross_file_permutation(tmp_path):
    """A selected branch uses the same inventory under every include order."""
    write(tmp_path / "choice.sie", """
        @if (true) {
            @type Word = Record;
        } @else {
            @type Word = Missing;
        }
    """)
    write(tmp_path / "record.sie", "struct Record { number: i32; }")

    states = set()
    for order in permutations(("choice", "record")):
        root = write(tmp_path / "main.sie", f"""
            @include("{order[0]}");
            @include("{order[1]}");
            fn main() -> i32 {{ let value: Word = {{42}}; return value.number; }}
        """)
        states.add(compile_files((root,)))

    assert len(states) == 1


def test_invalid_cross_file_permutations_keep_the_same_diagnostic(tmp_path):
    """Entry-source order cannot choose a namespace-collision diagnostic."""
    alias = write(tmp_path / "alias.sie", "@type Shared = i32;")
    record = write(tmp_path / "record.sie", "struct Shared {}")
    messages = set()

    for order in permutations((alias, record)):
        with pytest.raises(TypeError) as info:
            compile_files(order)
        messages.add(str(info.value))

    assert messages == {"type 'Shared' is declared more than once"}


def test_entry_sources_do_not_form_separate_type_namespaces(tmp_path):
    """Multiple command-line sources remain one C-style compilation unit."""
    first = write(tmp_path / "first.sie", "struct Shared {}")
    second = write(tmp_path / "second.sie", "struct Shared {}")

    with pytest.raises(TypeError, match="declared more than once"):
        compile_files((first, second))
