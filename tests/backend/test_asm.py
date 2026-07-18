"""Feature tests for '@asm' assembly, functions and inline blocks."""

import pytest


def test_asm_function(run):
    """
    An '@asm' function's params feed the assembly, '${out}' its return.
    """
    result = run("""
        @if (TARGET_ARCH == ARCH_AARCH64) {
            @asm
            fn add2(x: i32, y: i32) -> i32 {
                add ${out:w}, ${x:w}, ${y:w}
            }
        } @else {
            @asm
            fn add2(x: i32, y: i32) -> i32 {
                movl ${x:k}, ${out:k}
                addl ${y:k}, ${out:k}
            }
        }

        fn main() -> i32 {
            return add2(30, 12);
        }
    """)
    assert result.returncode == 42


def test_inline_block_produces_a_value(run):
    """
    '@asm (args) -> T { ... }' is an expression; '$name' interpolates
    without braces too.
    """
    result = run("""
        @if (TARGET_ARCH == ARCH_AARCH64) {
            fn combine(x: i64, y: i64) -> i64 {
                return @asm (x, y) -> i64 { add $out, $x, $y };
            }
        } @else {
            fn combine(x: i64, y: i64) -> i64 {
                return @asm (x, y) -> i64 {
                    movq $x, $out
                    addq $y, $out
                };
            }
        }

        fn main() -> i32 {
            let sum: i64 = combine(30, 12);
            return sum as i32;
        }
    """)
    assert result.returncode == 42


def test_statement_form_and_clobbers(run):
    """
    A bare '@asm { ... }' embeds assembly as a statement; '@clobbers'
    rides along.
    """
    result = run("""
        fn main() -> i32 {
            @asm { nop }
            @asm @clobbers("memory") { nop }
            return 42;
        }
    """)
    assert result.returncode == 42


def test_plain_dollars_escape(run):
    """
    A '$' that isn't an operand (an immediate, say) passes through.
    """
    result = run("""
        @if (TARGET_ARCH == ARCH_AARCH64) {
            @asm fn answer() -> i32 { mov ${out:w}, #42 }
        } @else {
            @asm fn answer() -> i32 { movl $42, ${out:k} }
        }

        fn main() -> i32 { return answer(); }
    """)
    assert result.returncode == 42


def test_template_and_constraints_in_the_ir(compile_source):
    """
    Operands number output-first, constraints pair '=r' with inputs, and
    clobbers append as '~{...}'.
    """
    module = str(compile_source("""
        fn main() -> i32 {
            let x: i32 = 1;
            return @asm @clobbers("x9", "memory") (x) -> i32 {
                mov ${out:w}, ${x:w}
            };
        }
    """))
    assert 'asm sideeffect "mov ${0:w}, ${1:w}", "=r,r,~{x9},~{memory}"' in module


def test_unknown_operand_is_an_error(compile_source):
    """
    Interpolating a name that isn't an operand fails at compile time.
    """
    with pytest.raises(TypeError, match="unknown assembly operand 'nope'"):
        compile_source("""
            fn main() -> i32 {
                return @asm () -> i32 { mov ${out:w}, ${nope:w} };
            }
        """)


def test_out_is_reserved_when_returning(compile_source):
    """
    An operand named 'out' collides with the return register.
    """
    with pytest.raises(TypeError, match="duplicate assembly operand 'out'"):
        compile_source("""
            fn main() -> i32 {
                let out: i32 = 1;
                return @asm (out) -> i32 { mov ${out:w}, ${out:w} };
            }
        """)


def test_clobbers_requires_asm(compile_source):
    """
    '@clobbers' describes an assembly body, nothing else.
    """
    with pytest.raises(SyntaxError, match="'@clobbers' requires '@asm'"):
        compile_source('@clobbers("x0") fn f() { }')


def test_asm_function_needs_a_body(compile_source):
    """
    An '@asm' function cannot be a bodiless declaration.
    """
    with pytest.raises(SyntaxError, match="needs an assembly body"):
        compile_source("@asm fn f() -> i32;")


def test_aggregate_operands_are_rejected(compile_source):
    """
    Only scalars and pointers travel in registers.
    """
    with pytest.raises(TypeError, match="only scalars and pointers"):
        compile_source("""
            struct S { a: i32; }

            fn main() -> i32 {
                let s: S = { a = 1 };
                @asm (s) { nop }
                return 0;
            }
        """)
