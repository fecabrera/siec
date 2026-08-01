"""Feature tests for Clone, AssignFrom, Assign, and explicit move."""

import pytest


def test_assignment_selects_borrowed_consuming_and_clone_paths(run):
    """RHS ownership selects the matching interface and preserves borrows."""
    source = """
    struct Value: Clone, AssignFrom<Value>, Assign<Value> {
        value: i32;
    }

    fn Value::clone(const &self) -> Value {
        return { self.value + 100 };
    }

    fn Value::assign_from(&self, source: const &Value) {
        self.value = source.value + 10;
    }

    fn Value::assign(&self, source: Value) {
        self.value = source.value + 20;
    }

    struct Copy: Clone { value: i32; }

    fn Copy::clone(const &self) -> Copy {
        return { self.value + 5 };
    }

    fn make(value: i32) -> Value { return { value }; }

    fn main() -> i32 {
        let a: Value = { 1 };
        let b: Value = { 2 };

        a = b;
        if (a.value != 12 or b.value != 2) { return 1; }

        a = move b;
        if (a.value != 22) { return 2; }

        a = make(3);
        if (a.value != 23) { return 3; }

        a = { 4 };
        if (a.value != 24) { return 4; }

        let copy: Copy = { 0 };
        let source: Copy = { 37 };
        copy = source;
        return copy.value;
    }
    """
    assert run(source).returncode == 42


def test_assignment_interfaces_support_different_source_types(run):
    """AssignFrom and Assign use their claimed RHS type independently."""
    source = """
    struct Number: AssignFrom<i32>, Assign<i64> { value: i64; }

    fn Number::assign_from(&self, source: const &i32) {
        self.value = source as i64 + 1;
    }

    fn Number::assign(&self, source: i64) {
        self.value = source + 2;
    }

    fn main() -> i32 {
        let number: Number = { 0 };
        let borrowed: i32 = 39;
        number = borrowed;
        if (number.value != 40) { return 1; }

        let consumed: i64 = 40;
        number = move consumed;
        return number.value as i32;
    }
    """
    assert run(source).returncode == 42


def test_assignment_interface_source_accepts_implementers(run):
    """A claimed interface source accepts each concrete implementer."""
    source = """
    interface Sequence<T>;
    fn Sequence<T>::length(const &self) -> u64;

    struct Text: Sequence<char> { length: u64; }
    fn Text::length(const &self) -> u64 { return self.length; }

    struct Buffer: AssignFrom<Sequence<char>> { length: u64; }
    fn Buffer::assign_from(&self, source: const &Sequence<char>) {
        self.length = source.length();
    }

    fn copy(source: const &Sequence<char>) -> i32 {
        let target: Buffer = { 0 };
        target = source;
        return target.length as i32;
    }

    fn main() -> i32 {
        let source: Text = { 42 };
        return copy(source);
    }
    """
    assert run(source).returncode == 42


def test_assign_from_accepts_numeric_widening(run):
    """Assignment materializes widened values for const-reference actions."""
    source = """
    struct Wide: AssignFrom<i64> { value: i64; }

    fn Wide::assign_from(&self, source: const &i64) {
        self.value = source;
    }

    fn main() -> i32 {
        let target: Wide = { 0 };
        target = 40;
        let source: i32 = 42;
        target = source;
        return target.value as i32;
    }
    """
    assert run(source).returncode == 42


def test_assign_from_temporary_resolves_generic_constructor(run):
    """Borrowing a constructed temporary resolves all code before emission."""
    source = """
    struct Value<T> { value: T; }
    fn Value<T>::init(&self, value: T) { self.value = value; }

    struct Holder: AssignFrom<Value<i32>> { value: i32; }
    fn Holder::assign_from(&self, source: const &Value<i32>) {
        self.value = source.value;
    }

    fn main() -> i32 {
        let target: Holder = { 0 };
        target = Value<i32>(42);
        return target.value;
    }
    """
    assert run(source).returncode == 42


def test_compound_fallback_uses_consuming_assignment(run):
    """A binary operator's temporary result reaches Assign<Self>."""
    source = """
    struct Number: Add<Number, i32>, Assign<Number> { value: i32; }

    fn Number::add(const &self, source: const i32) -> Number {
        return { self.value + source };
    }

    fn Number::assign(&self, source: Number) {
        self.value = source.value + 1;
    }

    fn main() -> i32 {
        let number: Number = { 40 };
        number += 1;
        return number.value;
    }
    """
    assert run(source).returncode == 42


@pytest.mark.parametrize(
    "source",
    [
        """
        struct Broken: Clone {}
        fn Broken::clone(const &self) -> i32 { return 0; }
        fn main() -> i32 { return 0; }
        """,
        """
        struct Broken: AssignFrom<i32> {}
        fn Broken::assign_from(&self, source: i32) {}
        fn main() -> i32 { return 0; }
        """,
        """
        struct Broken: Assign<i32> {}
        fn Broken::assign(&self, source: const &i32) {}
        fn main() -> i32 { return 0; }
        """,
    ],
)
def test_assignment_interface_claims_check_their_contracts(
        compile_source, source):
    """Each builtin assignment interface enforces its declared signature."""
    with pytest.raises(TypeError, match="does not implement"):
        compile_source(source)


def test_move_requires_an_owned_assignable_place(compile_source):
    """A temporary is already consumable and cannot be explicitly moved."""
    with pytest.raises(TypeError, match="requires an owned local variable"):
        compile_source("""
        fn main() -> i32 {
            let value: i32 = 0;
            value = move 42;
            return value;
        }
        """)


def test_move_invalidates_its_source_and_rejects_self_move(compile_source):
    """An explicitly transferred local cannot be read or transferred twice."""
    with pytest.raises(TypeError, match="use of moved value 'source'"):
        compile_source("""
        struct Value { value: i32; }
        fn main() -> i32 {
            let target: Value = { 0 };
            let source: Value = { 42 };
            target = move source;
            return source.value;
        }
        """)

    with pytest.raises(TypeError, match="cannot move 'value' into itself"):
        compile_source("""
        struct Value { value: i32; }
        fn main() -> i32 {
            let value: Value = { 42 };
            value = move value;
            return value.value;
        }
        """)


def test_assignment_reinitializes_a_moved_local(run):
    """Direct replacement makes previously moved storage initialized again."""
    source = """
    struct Value { value: i32; }
    fn main() -> i32 {
        let first: Value = { 1 };
        let second: Value = { 2 };
        second = move first;
        first = { 42 };
        return first.value;
    }
    """
    assert run(source).returncode == 42


def test_move_state_flows_out_of_nested_control_flow(compile_source):
    """A move on any continuing branch invalidates later reads."""
    with pytest.raises(TypeError, match="use of moved value 'source'"):
        compile_source("""
        struct Value { value: i32; }
        fn main() -> i32 {
            let target: Value = { 0 };
            let source: Value = { 42 };
            if (target.value == 0) {
                target = move source;
            }
            return source.value;
        }
        """)
