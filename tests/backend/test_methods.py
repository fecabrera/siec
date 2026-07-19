"""Feature tests for struct methods: 'fn S::m(self: &S)' and its calls."""

import pytest


def test_methods_act_on_the_instance(run):
    """
    Both call forms reach the method, and the '&S' receiver mutates the
    instance itself, not a copy.
    """
    source = """
    struct Counter { count: i32; }

    fn Counter::bump(self: &Counter, by: i32) {
        self.count += by;
    }

    fn Counter::value(self: const &Counter) -> i32 {
        return self.count;
    }

    fn main() -> i32 {
        let c: Counter = { 0 };

        c.bump(40);
        Counter::bump(c, 2);

        let v = c.value();  // inferred return type
        return v + Counter::value(c) - 42;
    }
    """
    assert run(source).returncode == 42


def test_const_receivers_follow_the_contract(run, compile_source):
    """
    A 'const &S' method works on a const instance; a mutating '&S' one
    is rejected there.
    """
    source = """
    struct P { x: i32; }

    fn P::get(self: const &P) -> i32 { return self.x; }

    fn f(p: const P) -> i32 { return p.get(); }

    fn main() -> i32 { let p: P = { 42 }; return f(p); }
    """
    assert run(source).returncode == 42

    with pytest.raises(TypeError, match="cannot bind a 'const Counter' value "
                                        "to a mutable '&Counter'"):
        compile_source("""
        struct Counter { count: i32; }
        fn Counter::bump(self: &Counter) { self.count += 1; }
        fn f(c: const Counter) { c.bump(); }
        """)


def test_generic_struct_methods_stamp_with_the_struct(run):
    """
    'fn Stack<T>::push' instantiates per 'Stack<i32>', receivers chain
    through fields, and methods call each other on self.
    """
    source = """
    struct Stack<T> {
        items: T*;
        top: i64;
    }

    fn Stack<T>::push(self: &Stack<T>, item: T) {
        self.items[self.top] = item;
        self.top += 1;
    }

    fn Stack<T>::pop(self: &Stack<T>) -> T {
        self.top -= 1;
        return self.items[self.top];
    }

    fn Stack<T>::replace(self: &Stack<T>, item: T) -> T {
        let old = self.pop();
        self.push(item);
        return old;
    }

    struct Wrap { inner: Stack<i32>; }

    fn main() -> i32 {
        let backing: @raw<i32>[8];
        let s: Stack<i32> = { &backing[0], 0 };

        s.push(40);
        s.push(1);

        let w: Wrap = { s };
        let x = w.inner.pop();          // chained receiver

        return x + s.replace(2) + s.pop() + s.pop() - 2; // 1+1+2+40-2
    }
    """
    assert run(source).returncode == 42


def test_generic_methods_infer_and_spell_their_arguments(run):
    """
    A method's own '<T>' resolves like a generic function's: inferred
    from arguments, or spelled 's.m<i64>(...)'.
    """
    source = """
    struct Vec2 { x: i32; y: i32; }

    fn Vec2::scale<T>(self: const &Vec2, by: T) -> T {
        return ((self.x + self.y) as T) * by;
    }

    fn main() -> i32 {
        let v: Vec2 = { 3, 4 };

        let a = v.scale(5 as i64);       // T inferred: i64
        let b = v.scale<i32>(1);         // T spelled

        return a as i32 + b; // 35 + 7
    }
    """
    assert run(source).returncode == 42


def test_bad_receivers_are_rejected(compile_source):
    """
    The first parameter must be the receiver reference, and a missing
    method names itself.
    """
    with pytest.raises(TypeError, match="first parameter must be its "
                                        "receiver: '&P' or 'const &P'"):
        compile_source("""
        struct P { x: i32; }
        fn P::get(self: P) -> i32 { return self.x; }
        """)

    with pytest.raises(NameError, match="undefined function 'p.missing'"):
        compile_source("""
        struct P { x: i32; }
        fn main() -> i32 { let p: P = { 1 }; return p.missing(); }
        """)


def test_reference_returns_alias_the_receivers_storage(run):
    """
    'fn S::get(...) -> &T' yields assignable storage: methods chain on
    the result, and reading it copies the value out.
    """
    source = """
    struct Box { value: i32; }

    struct Pair {
        a: Box;
        b: Box;
    }

    fn Box::bump(self: &Box, by: i32) { self.value += by; }

    fn Pair::pick(self: &Pair, first: bool) -> &Box {
        if (first) { return self.a; }
        return self.b;
    }

    fn main() -> i32 {
        let p: Pair = { { 10 }, { 20 } };

        p.pick(true).bump(30);       // method on the returned reference
        let copy = p.pick(false);    // reading copies the Box out
        copy.value = 0;              // ...so this can't touch p.b

        return p.pick(true).value + p.b.value + 2; // 40 + 20 + 2... 
    }
    """
    assert run(source).returncode == 62


def test_generic_reference_returns_chain(run):
    """
    'List<T>::get -> &T' chains through nested instantiations.
    """
    source = """
    struct Slot<T> { value: T; }

    struct Grid<T> {
        cell: Slot<T>;
    }

    fn Slot<T>::set(self: &Slot<T>, value: T) { self.value = value; }

    fn Grid<T>::at(self: &Grid<T>) -> &Slot<T> {
        return self.cell;
    }

    fn main() -> i32 {
        let g: Grid<i32>;
        g.at().set(42);
        return g.at().value;
    }
    """
    assert run(source).returncode == 42
