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


def test_missing_method_names_itself(compile_source):
    """
    Calling a method the receiver's type does not declare is an error.
    """
    with pytest.raises(NameError, match="undefined function 'p.missing'"):
        compile_source("""
        struct P { x: i32; }
        fn main() -> i32 { let p: P = { 1 }; return p.missing(); }
        """)


def test_static_methods(run):
    """
    A method without a receiver first parameter is static: it is called
    through the type ('S::m(...)', 'S<T>::m(...)') or through an
    instance, which joins no arguments either way.
    """
    source = """
    struct Counter { count: i32; }
    fn Counter::init(self: &Counter, start: i32) { self.count = start; }
    fn Counter::make(start: i32) -> Counter { return Counter(start + 1); }

    struct Box<T> { value: T; }
    fn Box<T>::init(self: &Box<T>, value: T) { self.value = value; }
    fn Box<T>::of(value: T) -> Box<T> { return Box<T>(value); }

    struct Math { pad: i32; }
    fn Math::max<T>(a: T, b: T) -> T { return a > b ? a : b; }

    @type IntBox = Box<i32>;

    fn main() -> i32 {
        let a = Counter::make(10);      // qualified static
        let b = a.make(20);             // static through an instance

        let x = Box<i32>::of(4);        // generic-instance qualified static
        let y = x.of(2);                // and through an instance
        let z = IntBox::of(1);          // through an alias

        let m = Math::max(1, 2);        // generic static, inferred
        // 11 + 21 + 4 + 2 + 1 + 2 + 1
        return a.count + b.count + x.value + y.value + z.value + m
               + Math::max<i32>(0, 1) - 42;
    }
    """
    assert run(source).returncode == 0


def test_amp_self_receiver_sugar(run):
    """
    '&self' opens a method's parameters as sugar for 'self: &S', and
    'const &self' for 'self: const &S' — generic receivers included.
    """
    source = """
    struct Counter { count: i32; }
    fn Counter::init(&self, start: i32 = 0) { self.count = start; }
    fn Counter::bump(&self) { self.count += 1; }
    fn Counter::value(const &self) -> i32 { return self.count; }

    struct List<T> { length: u64; }
    fn List<T>::init(&self) { self.length = 0; }
    fn List<T>::push(&self, item: T) { self.length += 1; }
    fn List<T>::scale<U>(const &self, by: U) -> U {
        return (self.length as U) * by;
    }

    fn main() -> i32 {
        let c = Counter(40);
        c.bump();
        c.bump();

        let ro: const Counter = c;      // const receiver on a const instance

        let l = List<i32>();
        l.push(7);
        l.push(8);

        return ro.value() + (l.scale(0 as i64) as i32) - 42;
    }
    """
    assert run(source).returncode == 0


def test_method_references(run):
    """
    A bare 'S::m' or 'S<T>::m' is a function-reference value: an instance
    method takes its receiver as an ordinary '&S' argument, a static
    only its own, and a '&T' return loads through like a direct call.
    """
    source = """
    struct Counter { count: i32; }
    fn Counter::init(self: &Counter, start: i32) { self.count = start; }
    fn Counter::value(self: const &Counter) -> i32 { return self.count; }
    fn Counter::twice(n: i32) -> i32 { return n * 2; }

    struct Box<T> { value: T; }
    fn Box<T>::init(self: &Box<T>, value: T) { self.value = value; }
    fn Box<T>::get(self: &Box<T>) -> &T { return self.value; }

    fn apply(f: fn(i32) -> i32, n: i32) -> i32 { return f(n); }

    fn main() -> i32 {
        let c = Counter(10);
        let read = Counter::value;              // instance method
        let dbl: fn(i32) -> i32 = Counter::twice;   // static, annotated

        let b = Box<i32>(7);
        let get = Box<i32>::get;                // reference return

        // 10 + 22 + 6 + 7 - 45 + apply(...) - 0
        return read(c) + dbl(11) + apply(Counter::twice, 3)
               + get(b) - 45 + apply(dbl, 0);
    }
    """
    assert run(source).returncode == 0


def test_a_missing_method_reference_names_the_type(compile_source):
    """
    Referencing a method a struct does not declare names both precisely.
    """
    with pytest.raises(TypeError, match="type 'S' has no method 'missing'"):
        compile_source("""
        struct S { x: i32; }
        fn main() -> i32 { let f = S::missing; return 0; }
        """)


def test_a_static_init_cannot_construct(compile_source):
    """
    'S(...)' passes the instance as init's receiver, so a receiverless
    'init' leaves the type without a constructor.
    """
    with pytest.raises(TypeError, match="a static 'init' cannot construct"):
        compile_source("""
        struct P { x: i32; }
        fn P::init(start: i32) -> P { let p: P = { start }; return p; }
        fn main() -> i32 { let p = P(1); return p.x; }
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


def test_assignment_through_a_reference_return(run, compile_source):
    """
    A reference-returning call is assignable storage: plain and compound
    assignment store through it; a plain call's value is not.
    """
    source = """
    struct List<T> { data: T*; length: u64; }

    fn List<T>::get(self: &List<T>, index: u64) -> &T {
        return self.data[index];
    }

    fn main() -> i32 {
        let backing: @raw<i32>[4];
        let l: List<i32> = { &backing[0], 4 };

        l.get(0) = 30;
        l.get(0) += 10;
        l.get(1) = l.get(0) + 2;

        return l.get(0) + l.get(1) - 40; // 40 + 42 - 40
    }
    """
    assert run(source).returncode == 42

    with pytest.raises(TypeError, match="cannot take the address of a call's"):
        compile_source("""
        fn f(n: i32) -> i32 { return n; }
        fn main() -> i32 { f(1) = 2; return 0; }
        """)


def test_constructors_build_and_init(run, compile_source):
    """
    'S(args)' is the expression form of 'let s: S; s.init(args);': stack
    space, field defaults, then init — usable anywhere, arguments and
    method chains included.
    """
    source = """
    struct String {
        data: char* = null;
        length: u64;
    }

    fn String::init(self: &String) { self.length = 0; }

    struct List<T> {
        items: @raw<T>[8];
        length: u64;
    }

    fn List<T>::init(self: &List<T>) { self.length = 0; }

    fn List<T>::push(self: &List<T>, item: T) {
        self.items[self.length] = item;
        self.length += 1;
    }

    struct Counter { count: i32; }
    fn Counter::init(self: &Counter, start: i32) { self.count = start; }
    fn Counter::bump(self: &Counter) -> i32 { self.count += 1; return self.count; }

    fn take(c: Counter) -> i32 { return c.count; }

    fn main() -> i32 {
        let lst = List<String>();
        lst.push(String());          // constructor as an argument
        lst.push(String());

        if (lst.items[0].data != null) { return 1; } // defaults applied

        return take(Counter(38))     // constructor as an argument
            + Counter(0).bump()      // method chain on the temporary
            + lst.length as i32 + 1;
    }
    """
    assert run(source).returncode == 42

    with pytest.raises(TypeError, match="type 'P' has no 'init' method"):
        compile_source("""
        struct P { x: i32; }
        fn main() -> i32 { let p = P(); return 0; }
        """)

    with pytest.raises(TypeError, match="generic struct 'Box' needs its type "
                                        "arguments"):
        compile_source("""
        struct Box<T> { v: T; }
        fn Box<T>::init(self: &Box<T>) { }
        fn main() -> i32 { let b = Box(); return 0; }
        """)
