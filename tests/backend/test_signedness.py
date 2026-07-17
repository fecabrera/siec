"""Feature tests for signed vs unsigned integer semantics."""


def test_unsigned_division_treats_the_high_bit_as_magnitude(run):
    """
    u8 254 / 2 is 127; a signed division of the same bits would give 255.
    """
    source = """
    fn main() -> i32 {
        let u: u8 = 254;
        u /= 2;
        if (u == 127) {
            return 1;
        }
        return 0;
    }
    """
    assert run(source).returncode == 1


def test_unsigned_comparison_treats_the_high_bit_as_magnitude(run):
    """
    u8 200 > 100 is true; read as signed, 200's bits are -56 and the test would fail.
    """
    source = """
    fn main() -> i32 {
        let big: u8 = 200;
        if (big > 100) {
            return 1;
        }
        return 0;
    }
    """
    assert run(source).returncode == 1


def test_unsigned_right_shift_is_logical(run):
    """
    An unsigned right shift fills with zeros rather than the sign bit.
    """
    source = """
    fn main() -> i32 {
        let u: u8 = 240; // 1111_0000
        u >>= 4;         // logical: 0000_1111 = 15
        if (u == 15) {
            return 1;
        }
        return 0;
    }
    """
    assert run(source).returncode == 1


def test_signed_division_rounds_toward_zero(run):
    """
    Signed division and remainder keep the dividend's sign.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = -7;
        return a / 2 + 100; // -3 + 100 = 97
    }
    """
    assert run(source).returncode == 97


def test_integer_literals_adapt_to_either_signedness(run):
    """
    A literal combines with signed and unsigned operands alike.
    """
    source = """
    fn main() -> i32 {
        let s: i32 = 10;
        let u: u32 = 10;
        if (s + 1 == 11 and u + 1 == 11) {
            return 6;
        }
        return 0;
    }
    """
    assert run(source).returncode == 6
