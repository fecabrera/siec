"""Feature tests for nested functions and lexical closures."""

import pytest


def test_anonymous_closure_captures_outer_local(run):
    source = """
    fn main() -> i32 {
        let value = 40;
        let callback = () -> i32 => { return value + 2; };
        return callback();
    }
    """
    assert run(source).returncode == 42


def test_named_nested_function_captures_outer_local(run):
    source = """
    fn main() -> i32 {
        let value = 40;
        fn callback() -> i32 { return value + 2; }
        return callback();
    }
    """
    assert run(source).returncode == 42


def test_closure_parameter_keeps_environment(run):
    source = """
    fn invoke(callback: closure fn() -> i32) -> i32 {
        return callback();
    }

    fn main() -> i32 {
        let value = 42;
        return invoke(() -> i32 => { return value; });
    }
    """
    assert run(source).returncode == 42


def test_closure_adapts_to_callback_abi_with_environment(run):
    source = """
    fn invoke_foreign(callback: fn(i32, opaque*) -> i32,
                      data: opaque*) -> i32 {
        return callback(7, data);
    }

    fn main() -> i32 {
        let value = 42;
        let callback = () -> i32 => { return value; };
        return invoke_foreign(
            callback as fn(i32, opaque*) -> i32,
            callback.env
        );
    }
    """
    assert run(source).returncode == 42


def test_generic_macro_selects_callback_abi(run):
    source = """
    @macro CALLBACK<ABI>(callback) = callback as ABI;

    fn invoke_foreign(callback: fn(i32, opaque*) -> i32,
                      data: opaque*) -> i32 {
        return callback(7, data);
    }

    fn main() -> i32 {
        let value = 42;
        let callback = () -> i32 => { return value; };
        return invoke_foreign(
            CALLBACK<fn(i32, opaque*) -> i32>(callback),
            callback.env
        );
    }
    """
    assert run(source).returncode == 42


def test_closure_mutates_outer_local(run):
    source = """
    fn main() -> i32 {
        let value = 40;
        let increment = () => { value += 2; };
        increment();
        return value;
    }
    """
    assert run(source).returncode == 42


def test_returned_closure_keeps_captured_storage_alive(run):
    source = """
    fn make_callback() -> closure fn() -> i32 {
        let value = 40;
        return () -> i32 => {
            value += 1;
            return value;
        };
    }

    fn main() -> i32 {
        let callback = make_callback();
        callback();
        return callback();
    }
    """
    assert run(source).returncode == 42


def test_returned_closure_environment_works_through_foreign_abi(run):
    source = """
    fn invoke_foreign(callback: fn(opaque*) -> i32,
                      data: opaque*) -> i32 {
        return callback(data);
    }

    fn make_callback() -> closure fn() -> i32 {
        let value = 42;
        return () -> i32 => { return value; };
    }

    fn main() -> i32 {
        let callback = make_callback();
        return invoke_foreign(
            callback as fn(opaque*) -> i32,
            callback.env
        );
    }
    """
    assert run(source).returncode == 42


def test_method_accepts_arrow_closure(run):
    source = """
    struct Invoker {
        fn invoke(&self, callback: closure fn() -> i32) -> i32 {
            return callback();
        }
    }

    fn main() -> i32 {
        let invoker: Invoker;
        let value = 42;
        return invoker.invoke(() -> i32 => { return value; });
    }
    """
    assert run(source).returncode == 42


def test_let_arrow_closure_matches_nested_function(run):
    source = """
    fn main() -> i32 {
        let value = 40;
        let add = (amount: i32) => { value += amount; };
        add(2);
        return value;
    }
    """
    assert run(source).returncode == 42


def test_let_expression_bodied_arrow_closure(run):
    source = """
    fn invoke(callback: closure fn(i32)) {
        callback(2);
    }

    fn main() -> i32 {
        let value = 40;
        let add = (amount: i32) => value += amount;
        invoke(add);
        return value;
    }
    """
    assert run(source).returncode == 42


def test_let_expression_bodied_arrow_closure_can_return(run):
    source = """
    fn main() -> i32 {
        let add = (a: i32, b: i32) -> i32 => a + b;
        return add(40, 2);
    }
    """
    assert run(source).returncode == 42


def test_let_arrow_closure_expands_parameter_aliases(run):
    source = """
    @type Count = i32;

    fn invoke(callback: closure fn(i32)) {
        callback(2);
    }

    fn main() -> i32 {
        let value = 40;
        let add = (amount: Count) => { value += amount; };
        invoke(add);
        return value;
    }
    """
    assert run(source).returncode == 42


def test_method_let_arrow_closure_expands_parameter_aliases(run):
    source = """
    @type Count = i32;

    struct Host {
        fn connect(&self, callback: closure fn(i32)) {
            let handle = (amount: Count) => callback(amount);
            handle(2);
        }
    }

    fn main() -> i32 {
        let host: Host;
        let value = 40;
        host.connect((amount: i32) => { value += amount; });
        return value;
    }
    """
    assert run(source).returncode == 42


def test_callback_adapter_requires_environment_parameter(compile_source):
    with pytest.raises(TypeError, match="must end in an 'opaque\\*'"):
        compile_source("""
        fn main() {
            let callback = () => {};
            let raw = callback as fn(i32);
        }
        """)
