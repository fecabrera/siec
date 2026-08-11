"""Checked compile-time integer semantics and diagnostics."""

import pytest


def test_signed_division_and_remainder_match_generated_code(run):
    """Constant folding truncates toward zero and signs remainder by dividend."""
    source = """
    enum Folded {
        QUOTIENT = -3 / 2,
        REMAINDER = -3 % 2,
    }

    fn runtime(a: i32, b: i32) -> i32 {
        if (Folded::QUOTIENT == a / b and Folded::REMAINDER == a % b) {
            return 42;
        }
        return 0;
    }

    fn main() -> i32 { return runtime(-3, 2); }
    """
    assert run(source).returncode == 42


def test_constant_logical_operators_short_circuit(compile_source):
    """An unreachable constant operand is not resolved or evaluated."""
    compile_source("""
        @if (false and (1 / 0)) {
            @error("unreachable and arm");
        }
        @if (true or MISSING_CONSTANT) {
            @const ANSWER = 42;
        }
        @static_assert(false and MISSING_CONSTANT == 1 or true,
                       "short circuit failed");

        fn main() -> i32 { return ANSWER; }
    """)


@pytest.mark.parametrize(("expression", "message"), [
    ("1 / 0", "division by zero in constant expression"),
    ("1 % 0", "division by zero in constant expression"),
    ("1 << -1", "shift count cannot be negative"),
    ("1 >> 128", "shift count cannot exceed 31"),
    ("0x7fffffff * 2", "constant value 4294967294 does not fit i32"),
])
def test_invalid_constant_operations_are_diagnostics(
        compile_source, expression, message):
    """Invalid Python integer operations become located compiler errors."""
    with pytest.raises(TypeError, match=message) as info:
        compile_source(f"""
            enum Broken {{ VALUE = {expression} }}
            fn main() -> i32 {{ return 0; }}
        """)
    assert info.value.sie_line == 2


@pytest.mark.parametrize("source", [
    "@if (1 / 0) { fn picked() {} } fn main() {}",
    "@static_assert(1 % 0, \"invalid\"); fn main() {}",
    "fn main() { let values: i32[1 << -1]; }",
])
def test_constant_failures_are_consistent_across_contexts(
        compile_source, source):
    """Conditionals, assertions, and array sizes use the checked evaluator."""
    with pytest.raises(TypeError, match="constant expression|shift count"):
        compile_source(source)


def test_context_free_constant_overflow_is_a_diagnostic(compile_source):
    """Even without a narrower enum backing, constants stop at 128 bits."""
    with pytest.raises(TypeError, match="integer overflow in constant expression"):
        compile_source("""
            @static_assert((1 << 127) * 2, "too wide");
            fn main() {}
        """)


@pytest.mark.parametrize(("declaration", "message"), [
    ("enum E: i8 { VALUE = 0 << 8 }", "shift count cannot exceed 7"),
    ("enum E: i8 { VALUE = 128 }", "constant value 128 does not fit i8"),
    ("enum E: u8 { VALUE = -1 }", "constant value -1 does not fit u8"),
    ("enum E: u8 { LAST = 255, OVERFLOW }",
     "constant value 256 does not fit u8"),
])
def test_enum_evaluation_honors_backing_width(
        compile_source, declaration, message):
    """Shifts, explicit values, and automatic increments use enum width."""
    with pytest.raises(TypeError, match=message):
        compile_source(declaration)
