"""Feature tests for pointer comparison compatibility."""

import pytest


def test_opaque_pointer_compares_with_every_pointer_type(compile_source):
    """opaque* erases the pointee type for every comparison operator."""
    compile_source("""
        struct S;

        fn compare(a: opaque*, b: u8*, c: S*) {
            a < b;
            a == b;
            a > c;
            a != c;
            b <= a;
            c >= a;
        }

        fn main() -> i32 { return 0; }
    """)


@pytest.mark.parametrize("comparison", ("b <= c", "b == c"))
def test_mismatched_typed_pointer_comparison_is_rejected(
        compile_source, comparison):
    """Typed pointers remain comparable only with the same pointer type."""
    with pytest.raises(TypeError, match="cannot apply"):
        compile_source(f"""
            struct S;

            fn compare(b: u8*, c: S*) {{
                {comparison};
            }}

            fn main() -> i32 {{ return 0; }}
        """)


def test_same_llvm_pointer_shape_does_not_hide_a_type_mismatch(compile_source):
    """char* and u8* are distinct even though both lower to i8*."""
    with pytest.raises(TypeError, match="cannot apply '==' to char\\* and u8\\*"):
        compile_source("""
            fn compare(text: char*, bytes: u8*) {
                text == bytes;
            }

            fn main() -> i32 { return 0; }
        """)
