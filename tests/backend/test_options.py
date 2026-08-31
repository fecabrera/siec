"""Feature tests for builtin optional values and checked decay."""

import pytest


def test_option_wrap_none_compare_and_checked_decay(run):
    source = """
    fn maybe(ok: bool) -> Option<i32> {
        if (ok) { return 41; }
        return None;
    }

    fn take(value: i32) -> i32 { return value + 1; }

    fn use(value: Option<i32>) -> i32 {
        if (value == None) { return 7; }
        return take(value);
    }

    fn main() -> i32 {
        if (use(maybe(false)) != 7) { return 1; }
        if (use(maybe(true)) != 42) { return 2; }

        let absent: Option<i32> = None<i32>;
        if (absent != None) { return 3; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_option_truth_check_enables_decay(run):
    source = """
    fn take(value: i32) -> i32 { return value; }

    fn use(value: Option<i32>) -> i32 {
        if (not value) { return -1; }
        return take(value);
    }

    fn main() -> i32 {
        let some: Option<i32> = 9;
        let absent: Option<i32> = None;
        if (use(some) != 9) { return 1; }
        if (use(absent) != -1) { return 2; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_checked_option_field_decays_to_reference_return(run):
    source = """
    @noreturn @extern fn exit(status: i32);

    struct Path { value: i32; }
    fn Path(value: i32) -> Path { return { value }; }

    struct Cursor { path: Option<Path>; }

    fn Cursor::next(&self) -> &Path {
        if (self.path)
            return self.path;
        exit(1);
    }

    fn main() -> i32 {
        let iterator: Cursor = { Path(42) };
        return iterator.next().value - 42;
    }
    """
    assert run(source).returncode == 0


def test_constructor_result_assigns_into_option(run):
    source = """
    struct Path { value: i32; }

    fn Path(value: i32) -> Path { return { value }; }

    struct Box { path: Option<Path> = None; }

    fn Box::set(&self) {
        self.path = Path(5);
    }

    fn main() -> i32 {
        let box: Box;
        box.set();
        if (box.path == None) { return 1; }
        return box.path.value.value - 5;
    }
    """
    assert run(source).returncode == 0


def test_option_conditionally_owns_and_destroys_its_value(run):
    source = """
    @static let destroyed: i32;

    struct Item: Destroy { value: i32; }

    fn Item(value: i32) -> Item { return { value }; }

    fn Item::destroy(&self) {
        destroyed += self.value;
    }

    fn exercise() -> i32 {
        let option: Option<Item> = Item(1);
        option = Item(10);                    // destroys Item(1)
        if (destroyed != 1) { return 100; }

        drop option;                          // destroys Item(10)
        if (destroyed != 11) { return 101; }

        {
            let scoped: Option<Item> = Item(4);
        }                                     // destroys Item(4)

        {
            let absent: Option<Item> = None;
        }                                     // destroys no Item

        return destroyed;
    }

    fn main() -> i32 { return exercise() - 15; }
    """
    assert run(source).returncode == 0


def test_option_of_plain_value_is_not_destroyable(compile_source):
    with pytest.raises(TypeError, match="type does not implement Destroy"):
        compile_source("""
        fn main() {
            let value: Option<i32> = 1;
            drop value;
        }
        """)


def test_unchecked_option_cannot_decay(compile_source):
    with pytest.raises(TypeError, match="check that it is present first"):
        compile_source("""
        fn take(value: i32) {}
        fn use(value: Option<i32>) { take(value); }
        """)


def test_option_value_member_is_guarded(compile_source):
    with pytest.raises(TypeError, match="option is absent or unchecked"):
        compile_source("""
        fn use(value: Option<i32>) -> i32 { return value.value; }
        """)


def test_generic_noreturn_check_enables_option_field_read(compile_source):
    """The selected generic callee carries noreturn flow past an early exit."""
    compile_source("""
    @noreturn @extern fn abort();

    @noreturn
    fn stop<T>(value: T) { abort(); }

    struct Box { value: Option<i32>; }

    fn Box::get(&self) -> i32 {
        if (self.value == None)
            stop(1);
        return self.value.value;
    }
    """)


def test_option_is_builtin(compile_source):
    with pytest.raises(TypeError, match="struct 'Option' is declared more"):
        compile_source("struct Option<T> { value: T; }")
