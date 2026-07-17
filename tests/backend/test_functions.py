"""Feature tests for functions: params, returns, recursion, and externs."""


def test_parameters_and_return(run):
    """
    A function receives arguments and returns a computed value.
    """
    source = """
    fn add(a: i32, b: i32) -> i32 {
        return a + b;
    }

    fn main() -> i32 {
        return add(30, 12);
    }
    """
    assert run(source).returncode == 42


def test_forward_declaration_then_definition(run):
    """
    A function may be called before the file defines its body.
    """
    source = """
    fn helper(n: i32) -> i32;

    fn main() -> i32 {
        return helper(21);
    }

    fn helper(n: i32) -> i32 {
        return n * 2;
    }
    """
    assert run(source).returncode == 42


def test_recursion(run):
    """
    A function may call itself; factorial of 5 is 120.
    """
    source = """
    fn fact(n: i32) -> i32 {
        if (n < 2) {
            return 1;
        }
        return n * fact(n - 1);
    }

    fn main() -> i32 {
        return fact(5);
    }
    """
    assert run(source).returncode == 120


def test_mutual_calls(run):
    """
    Functions can call one another regardless of declaration order.
    """
    source = """
    fn is_even(n: i32) -> i32 {
        if (n == 0) {
            return 1;
        }
        return is_odd(n - 1);
    }

    fn is_odd(n: i32) -> i32 {
        if (n == 0) {
            return 0;
        }
        return is_even(n - 1);
    }

    fn main() -> i32 {
        return is_even(10) + is_odd(7); // 1 + 1
    }
    """
    assert run(source).returncode == 2


def test_void_function_falls_off_the_end(run):
    """
    A function with no return type may fall off its end.
    """
    source = """
    fn noop() {
    }

    fn main() -> i32 {
        noop();
        return 8;
    }
    """
    assert run(source).returncode == 8


def test_main_without_a_return_type_exits_zero(run):
    """
    'fn main()' implicitly exits with code 0, from a bare return or the end.
    """
    source = """
    fn main() {
        return;
    }
    """
    assert run(source).returncode == 0


def test_main_args_form_receives_the_arguments(run):
    """
    'fn main(args: char*[])' gets argv wrapped as a fat array, program name included.
    """
    source = """
    fn main(args: char*[]) -> i32 {
        // the program name plus the two passed arguments
        return args.length as i32;
    }
    """
    assert run(source, "a", "b").returncode == 3


def test_extern_varargs_call_prints(run):
    """
    An extern varargs function (printf) links and runs, writing to stdout.
    """
    source = """
    @extern fn printf(fmt: char*, ...) -> i32;

    fn main() -> i32 {
        printf("n=%d\\n", 42);
        return 0;
    }
    """
    result = run(source)
    assert result.returncode == 0
    assert result.stdout == "n=42\n"
