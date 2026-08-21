"""Feature tests for deliberate function and method overrides."""

import pytest


def test_override_replaces_a_concrete_function_independent_of_order(run):
    """The override body wins even when it is written before its target."""
    source = """
    @override
    fn answer() -> i32 { return 42; }

    fn answer() -> i32 { return 1; }

    fn main() -> i32 { return answer(); }
    """
    assert run(source).returncode == 42


def test_concrete_array_method_overrides_a_receiver_family(run):
    """A concrete receiver replaces the family only for its own element."""
    source = """
    fn T[]::answer(const &self) -> i32 { return 1; }

    @override
    fn char[]::answer(const &self) -> i32 { return 42; }

    fn main() -> i32 {
        let chars: char[] = "x";
        let ints: i32[] = [1];
        return chars.answer() + ints.answer() - 1;
    }
    """
    assert run(source).returncode == 42


def test_concrete_override_matches_a_bounded_family_through_aliases(run):
    """Target matching canonicalizes aliases on both method signatures."""
    source = """
    @type Answer = i32;

    interface Special;
    @extend char: Special;

    @where<T: Special>
    @extend T[]: Special {
        fn answer(const &self) -> Answer { return 1; }
    }

    @override
    fn char[]::answer(const &self) -> Answer { return 42; }

    fn main() -> i32 {
        let chars: char[] = "x";
        return chars.answer();
    }
    """
    assert run(source).returncode == 42


def test_concrete_method_override_keeps_family_overloads(run):
    """An exact replacement does not hide differently shaped siblings."""
    source = """
    fn T[]::answer(const &self) -> i32 { return 1; }
    fn T[]::answer(const &self, fallback: i32) -> i32 { return fallback; }

    @override
    fn char[]::answer(const &self) -> i32 { return 42; }

    fn main() -> i32 {
        let chars: char[] = "x";
        return chars.answer() + chars.answer(1) - 1;
    }
    """
    assert run(source).returncode == 42


def test_concrete_generic_struct_method_overrides_its_family(run):
    """A concrete generic receiver is an exact method specialization."""
    source = """
    struct Box<T> { value: T; }

    fn Box<T>::answer(const &self) -> i32 { return 1; }

    @override
    fn Box<i32>::answer(const &self) -> i32 { return 42; }

    fn main() -> i32 {
        let number: Box<i32> = { 0 };
        let character: Box<char> = { 'x' };
        return number.answer() + character.answer() - 1;
    }
    """
    assert run(source).returncode == 42


def test_bounded_array_override_falls_back_outside_its_bound(run):
    """A bounded family override wins where eligible and leaves the base elsewhere."""
    source = """
    interface Special;
    @extend char: Special;

    fn T[]::answer(const &self) -> i32 { return 1; }

    @where<T: Special>
    @override
    fn T[]::answer(const &self) -> i32 { return 42; }

    fn main() -> i32 {
        let chars: char[] = "x";
        let ints: i32[] = [1];
        return chars.answer() + ints.answer() - 1;
    }
    """
    assert run(source).returncode == 42


def test_bounded_override_constrains_subset_of_receiver_parameters(run):
    """An override may bound K while preserving Map's unconstrained V."""
    source = """
    interface Special;
    struct Item {}
    @extend Item: Special;

    struct Map<K, V> {
        key: K;
        value: V;
    }

    fn Map<K, V>::answer(const &self) -> i32 { return 1; }

    @where<K: Special>
    @override
    fn Map<K, V>::answer(const &self) -> i32 { return 42; }

    fn main() -> i32 {
        let special: Map<Item, i32> = {{}, 0};
        let ordinary: Map<i32, char> = {0, 'x'};
        return special.answer() + ordinary.answer() - 1;
    }
    """
    assert run(source).returncode == 42


def test_nested_template_override_intersects_extension_bounds(run):
    """A nested override must satisfy both its own and its outer bound."""
    source = """
    interface Outer;
    interface Inner<T>;

    struct Both {}
    @extend Both: Outer, Inner<char>;

    struct OuterOnly {}
    @extend OuterOnly: Outer;

    struct Box<T> { value: T; }

    fn Box<T>::answer(const &self) -> i32 { return 1; }

    @where<T: Outer> {
        @extend Box<T>: Outer {
            @where<T: Inner<char>>
            @override
            fn answer(const &self) -> i32 { return 42; }
        }
    }

    fn main() -> i32 {
        let both: Box<Both> = {{}};
        let outer: Box<OuterOnly> = {{}};
        let plain: Box<i32> = {0};
        return both.answer() + outer.answer() + plain.answer() - 2;
    }
    """
    assert run(source).returncode == 42


def test_bounded_generic_function_override_is_more_specific(run):
    """The same override rule applies to constrained generic functions."""
    source = """
    interface Special;
    @extend char: Special;

    fn answer<T>(value: T) -> i32 { return 1; }

    @override
    fn answer<T: Special>(value: T) -> i32 { return 42; }

    fn main() -> i32 {
        return answer('x') + answer(1) - 1;
    }
    """
    assert run(source).returncode == 42


@pytest.mark.parametrize("declaration", [
    "@override fn answer() -> i32 { return 1; }",
    """
    @override
    fn T[]::answer(const &self) -> i32 { return 1; }
    """,
])
def test_override_requires_a_matching_target(compile_source, declaration):
    """An override cannot silently introduce a new function or family method."""
    with pytest.raises(TypeError, match="no matching declaration to override"):
        compile_source(declaration + "\nfn main() -> i32 { return 0; }")


def test_override_signature_must_match_its_target(compile_source):
    """Changing the return type is not an override of the family signature."""
    with pytest.raises(TypeError, match="no matching declaration to override"):
        compile_source("""
        fn T[]::answer(const &self) -> i32 { return 1; }

        @override
        fn T[]::answer(const &self) -> i64 { return 1; }

        fn main() -> i32 { return 0; }
        """)


def test_generic_override_return_must_match_its_target(compile_source):
    """A bounded override keeps the generic function's complete type."""
    with pytest.raises(TypeError, match="no matching declaration to override"):
        compile_source("""
        interface Special;

        fn answer<T>(value: T) -> i32 { return 1; }

        @override
        fn answer<T: Special>(value: T) -> i64 { return 1; }

        fn main() -> i32 { return 0; }
        """)


def test_concrete_function_cannot_be_overridden_twice(compile_source):
    """Two replacement bodies for one exact declaration are rejected."""
    with pytest.raises(TypeError, match="overridden more than once"):
        compile_source("""
        fn answer() -> i32 { return 1; }
        @override fn answer() -> i32 { return 2; }
        @override fn answer() -> i32 { return 3; }

        fn main() -> i32 { return answer(); }
        """)


def test_equally_specific_method_overrides_are_ambiguous(compile_source):
    """Overlapping bounds never make selection depend on declaration order."""
    with pytest.raises(TypeError, match="ambiguous"):
        compile_source("""
        interface Left;
        interface Right;
        @extend char: Left;
        @extend char: Right;

        fn T[]::answer(const &self) -> i32 { return 1; }
        @where<T: Left>
        @override fn T[]::answer(const &self) -> i32 { return 2; }
        @where<T: Right>
        @override fn T[]::answer(const &self) -> i32 { return 3; }

        fn main() -> i32 {
            let chars: char[] = "x";
            return chars.answer();
        }
        """)


def test_equally_specific_generic_overrides_are_ambiguous(compile_source):
    """Generic function overrides use the same deterministic ambiguity rule."""
    with pytest.raises(TypeError, match="ambiguous"):
        compile_source("""
        interface Left;
        interface Right;
        @extend char: Left;
        @extend char: Right;

        fn answer<T>(value: T) -> i32 { return 1; }
        @override fn answer<T: Left>(value: T) -> i32 { return 2; }
        @override fn answer<T: Right>(value: T) -> i32 { return 3; }

        fn main() -> i32 { return answer('x'); }
        """)
