"""Feature tests for floating-point literals and arithmetic."""


def test_float_literals_and_arithmetic(run):
    """
    Float literals initialize f32/f64 and combine with float instructions.
    """
    source = """
    fn main() -> i32 {
        let a: f64 = 3.5;
        let b: f64 = 1.5;

        let sum: f64 = a + b;      // 5.0
        let prod: f64 = a * 2.0;   // 7.0
        let quot: f64 = a / 0.5;   // 7.0
        let rem: f64 = 7.5 % 2.0;  // 1.5

        return (sum + prod + quot + rem) as i32; // 20
    }
    """
    assert run(source).returncode == 20


def test_negative_float_literal(run):
    """
    A '-' folds into the float literal, like it does for ints.
    """
    source = """
    fn main() -> i32 {
        let a: f64 = -2.5;
        return (-a * 2.0) as i32; // 5
    }
    """
    assert run(source).returncode == 5


def test_float_comparisons_and_truthiness(run):
    """
    Floats compare with float instructions and are truthy when nonzero.
    """
    source = """
    fn main() -> i32 {
        let pi: f64 = 3.14;
        let zero: f64 = 0.0;

        if (pi > 3.0 and not zero) {
            return 1;
        }
        return 0;
    }
    """
    assert run(source).returncode == 1


def test_int_literal_adapts_to_a_float_side(run):
    """
    An integer literal in a float context becomes a float constant.
    """
    source = """
    fn main() -> i32 {
        let x: f64 = 2.5;
        return (x * 2) as i32; // 5
    }
    """
    assert run(source).returncode == 5


def test_f32_widens_and_promotes(run):
    """
    An f32 widens to f64 implicitly, and floats pass through functions.
    """
    source = """
    fn double(x: f64) -> f64 {
        return x * 2.0;
    }

    fn main() -> i32 {
        let half: f32 = 0.5;
        let wide: f64 = half;
        return double(wide + 3.0) as i32; // 7
    }
    """
    assert run(source).returncode == 7


def test_float_output_reaches_printf(run):
    """
    Floats print through varargs, an f32 promoting to f64 C-style.
    """
    source = """
    @extern fn printf(fmt: char*, ...) -> i32;

    fn main() -> i32 {
        let half: f32 = 0.5;
        printf("%.2f %.2f\\n", half, 1.25);
        return 0;
    }
    """
    result = run(source)
    assert result.returncode == 0
    assert "0.50 1.25" in result.stdout
