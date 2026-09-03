"""Feature tests for checked binary-operation plans."""

import pytest


def test_binary_operations_are_selected_before_emission(
        monkeypatch, compile_source):
    """Emit lowers binary plans without semantic operator classification."""
    from siec.codegen import expressions, inference

    selected = {"semantic": 0, "legacy": 0}

    def guard(module, name, counter):
        original = getattr(module, name)

        def guarded(gen, *args, **kwargs):
            assert not gen.emitting
            selected[counter] += 1
            return original(gen, *args, **kwargs)

        monkeypatch.setattr(module, name, guarded)

    for name in (
            "operator_call", "option_none_test", "check_signedness",
            "arithmetic_type", "pointer_arithmetic_type"):
        guard(inference, name, "semantic")
    for name in ("check_signedness", "pointer_offset_parts", "match_widths"):
        guard(expressions, name, "legacy")

    compile_source("""
    @const SHIFT = 2;

    enum Flag: u8 {
        Read = 1,
    }

    struct Number: Add<Number, Number> {
        value: i32;
    }

    fn Number::add(&self, other: const &Number) -> Number {
        return { self.value + other.value };
    }

    fn main(argc: i32, argv: char**) -> i32 {
        let byte: u8 = 8;
        let mask: u64 = (15 as u64) % (1 << SHIFT);
        let pointer: char* = argv[0];
        let next = pointer + mask + @sizeof(i32);
        let denied = not byte & Flag::Read;
        let number: Number = { 1 };
        let sum = number + number;
        let absent: Option<i32> = None;
        if ((byte / 2 == 4) and mask == 3 and next != null and denied) {
            return 2 ** 3;
        }
        if (sum.value == 2 or absent != None) return 1;
        return 0;
    }
    """)

    assert selected["semantic"] > 0
    assert selected["legacy"] == 0


@pytest.mark.parametrize("expression,expected", [
    ("(40 as u64) + (1 + ONE)", 42),
    ("(44 as u64) - (1 + ONE)", 42),
    ("(21 as u64) * (1 + ONE)", 42),
    ("(84 as u64) / (1 + ONE)", 42),
    ("(86 as u64) % (43 + ONE)", 42),
    ("(2 as u64) ** (2 + ONE)", 8),
    ("(1 as u64) << (1 + ONE)", 4),
    ("(168 as u64) >> (1 + ONE)", 42),
    ("(43 as u64) & (40 + ONE + ONE)", 42),
    ("(40 as u64) | (1 + ONE)", 42),
    ("(43 as u64) ^ ONE", 42),
])
def test_every_builtin_operator_accepts_nested_adaptive_operands(
        run, expression, expected):
    """Each builtin operator gives nested literals its fixed operand type."""
    result = run(f"""
    @const ONE = 1;

    fn main() -> i32 {{
        let result: u64 = {expression};
        return result as i32;
    }}
    """)
    assert result.returncode == expected


@pytest.mark.parametrize("operator,expected", [
    ("==", 1),
    ("!=", 0),
    ("<", 0),
    (">", 0),
    ("<=", 1),
    (">=", 1),
])
def test_every_comparison_accepts_a_nested_adaptive_operand(
        run, operator, expected):
    """Each comparison gives a nested literal the fixed operand type."""
    result = run(f"""
    @const SHIFT = 2;

    fn main() -> i32 {{
        let value: u64 = 4;
        if (value {operator} (1 << SHIFT)) return 1;
        return 0;
    }}
    """)
    assert result.returncode == expected


@pytest.mark.parametrize("expression", [
    "pointer + count",
    "count + pointer",
    "pointer - count",
    "pointer + OFFSET",
    "pointer + (1 << SHIFT)",
    "pointer + count + OFFSET",
])
def test_pointer_offsets_accept_each_integer_operand_form(
        compile_source, expression):
    """Pointer plans accept fixed, constant, nested, and reversed offsets."""
    compile_source(f"""
    @const OFFSET = @sizeof(i32);
    @const SHIFT = 2;

    fn offset(pointer: u8*, count: u64) -> u8* {{
        return {expression};
    }}

    fn main() -> i32 {{ return 0; }}
    """)


def test_value_like_operands_adopt_char_bool_and_numeric_contexts(run):
    """Literals and enum members retain their established adaptive widths."""
    result = run("""
    enum Flag: u8 {
        Read = 1,
    }

    fn main() -> i32 {
        let mask: u64 = (40 as u64) | Flag::Read | 2;
        if ('A' != 65 or 65 != 'A') return 1;
        if (not false & Flag::Read and mask == 43) return 42;
        return 2;
    }
    """)
    assert result.returncode == 42


@pytest.mark.parametrize("declarations,expression,message", [
    ("let a: u64 = 1; let b: i64 = 1;", "a + b", "unsigned and signed"),
    ("let a: char = 'a'; let b: i32 = 1;", "a + b", "'char' operand"),
    ("let a: f64 = 1.0; let b: f64 = 2.0;", "a & b", "float operands"),
    ("let a: f64 = 2.0; let b: f64 = 2.0;", "a ** b", "float operands"),
    ("let a: u8* = null; let b: u8* = null;", "a + b", "two pointer"),
    ("let a: u64 = 1; let b: u8* = null;", "a - b", "subtract a pointer"),
    ("let a: u8* = null; let b: f64 = 1.0;", "a + b", "integer offset"),
    ("let a: u8* = null; let b: i8* = null;", "a == b", "cannot apply"),
])
def test_invalid_binary_plan_combinations_fail_during_check(
        compile_source, declarations, expression, message):
    """Check rejects each invalid scalar and pointer operand combination."""
    with pytest.raises(TypeError, match=message):
        compile_source(f"""
        fn main() -> i32 {{
            {declarations}
            {expression};
            return 0;
        }}
        """)


def test_binary_index_is_checked_when_used_only_as_an_address(
        compile_source):
    """An index below '&' receives its binary plan before Emit."""
    compile_source("""
    @const HEADER_SIZE = 8;

    @extern fn consume(value: u8*) -> bool;

    fn pass_address(buffer: &u8[], written: i32) -> bool {
        return consume(&buffer[HEADER_SIZE + written]);
    }

    fn main() -> i32 { return 0; }
    """)
