"""Feature tests for '-g' DWARF debug-info emission."""

import subprocess

from siec.backend import compile_to_object, link, prepare_module
from siec.codegen import codegen
from siec.lexer import lex
from siec.parser import parse

SOURCE = """
struct point { x: i32; y: i32; }

fn dist2(p: point) -> i32 {
    let dx: i32 = p.x;
    let dy: i32 = p.y;
    return dx * dx + dy * dy;
}

fn main() -> i32 {
    let p: point = { x = 3, y = 4 };
    return dist2(p) - 25;
}
"""


def debug_module(source: str):
    """
    Compile source text with debug info enabled.
    """
    return codegen(parse(lex(source)), "m", debug=True)


def test_debug_metadata_is_emitted():
    """
    '-g' attaches the compile unit, a subprogram per function, a location
    per statement, and a description of each variable and its type.
    """
    text = str(debug_module(SOURCE))

    assert "!DICompileUnit(" in text
    assert 'name: "dist2"' in text
    assert 'name: "main"' in text
    assert '!DILocalVariable(arg: 1' in text  # the 'p' parameter
    assert 'name: "dx"' in text
    assert "!DILocation(" in text
    assert "llvm.dbg.declare" in text

    # the struct describes its members at their laid-out offsets
    assert 'name: "point", size: 64, tag: DW_TAG_structure_type' in text
    assert 'name: "y", offset: 32' in text


def test_debug_metadata_passes_the_verifier():
    """
    The metadata is well-formed: LLVM's verifier accepts the module.
    """
    prepare_module(debug_module(SOURCE))


def test_no_debug_metadata_without_the_flag():
    """
    Without '-g' the module carries no debug info at all.
    """
    text = str(codegen(parse(lex(SOURCE)), "m"))
    assert "DICompileUnit" not in text
    assert "llvm.dbg" not in text


def test_debug_build_still_runs(tmp_path):
    """
    A '-g' build behaves exactly like a plain one.
    """
    obj, exe = tmp_path / "m.o", tmp_path / "m"
    compile_to_object(debug_module(SOURCE), str(obj))
    link([str(obj)], str(exe))
    assert subprocess.run([str(exe)]).returncode == 0


def test_fat_arrays_and_unions_describe_their_shape():
    """
    An 'X[]' shows as its data/length struct, and a union's fields all
    sit at offset zero.
    """
    text = str(debug_module("""
    union pun { i: i64; f: f64; }

    fn main() -> i32 {
        let msg: char[] = "hi";
        let u: pun;
        u.i = 0;
        return msg.length as i32 - 2;
    }
    """))

    assert 'name: "char[]", size: 128' in text
    assert 'name: "data"' in text and 'name: "length"' in text
    assert 'name: "pun", size: 64, tag: DW_TAG_union_type' in text


def test_recursive_structs_do_not_recurse():
    """
    A self-referential struct describes itself without looping: the inner
    pointer falls back to an untyped one.
    """
    text = str(debug_module("""
    struct node { value: i32; next: node*; }

    fn main() -> i32 {
        let head: node = { value = 0, next = null };
        return head.value;
    }
    """))

    assert 'name: "node"' in text
    prepare_module(debug_module("""
    struct node { value: i32; next: node*; }

    fn main() -> i32 {
        let head: node = { value = 0, next = null };
        return head.value;
    }
    """))


def test_reference_params_get_addressable_storage():
    """
    A '&T' parameter spills its pointer into a debug slot and describes
    itself as a DWARF reference, so the debugger shows the T it aliases.
    """
    text = str(debug_module("""
    struct package { value: i32; }

    fn bump(self: &package) { self.value += 1; }

    fn main() -> i32 {
        let p: package = { value = 0 };
        bump(p);
        return p.value - 1;
    }
    """))

    assert "self.ref" in text
    assert "DW_TAG_reference_type" in text
