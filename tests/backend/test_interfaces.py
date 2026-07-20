"""Feature tests for interfaces: abstract types with nominal conformance."""

import pytest


def test_interface_parameters_take_any_implementer(run):
    """
    'fn f(n: Named)' stamps per concrete argument type; fields and
    actions of the interface are usable in the body, and two interface
    parameters take two independent implementers.
    """
    source = """
    interface Named {
        name: char[];
    }

    fn Named::greet(self: &Named) -> char[];

    struct Person: Named {
        name: char[];
        age: i32;
    }
    fn Person::greet(self: &Person) -> char[] { return self.name; }

    struct Robot: Named {
        name: char[];
        serial: u64;
    }
    fn Robot::greet(&self) -> char[] { return self.name; }

    fn describe(n: Named) -> u64 {
        return n.greet().length + n.name.length;
    }

    fn both(a: Named, b: Named) -> u64 {
        return describe(a) + describe(b);
    }

    fn main() -> i32 {
        let p: Person = { "ada", 36 };
        let r: Robot = { "r2", 2 };

        if (describe(p) != 6) { return 1; }
        if (describe(r) != 4) { return 2; }
        if (both(p, r) != 10) { return 3; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_generic_interfaces(run):
    """
    'interface Iterator<T>;' with a generic action, implemented by a
    generic struct, consumed through an 'Iterator<i32>' parameter.
    """
    source = """
    interface Iterator<T>;

    fn Iterator<T>::next(self: &Iterator<T>, value: &T) -> bool;

    struct ArrayIterator<T>: Iterator<T> {
        arr: T[];
        index: u64;
    }

    fn ArrayIterator<T>::init(&self, arr: T[]) {
        self.arr = arr;
        self.index = 0;
    }

    fn ArrayIterator<T>::next(&self, value: &T) -> bool {
        if (self.index >= self.arr.length) {
            return false;
        }
        value = self.arr[self.index];
        self.index += 1;
        return true;
    }

    fn sum(it: Iterator<i32>) -> i32 {
        let total = 0;
        let v: i32;
        while (it.next(v)) {
            total += v;
        }
        return total;
    }

    fn main() -> i32 {
        let nums: i32[] = [10, 12, 20];
        let it = ArrayIterator<i32>(nums);
        return sum(it) - 42;
    }
    """
    assert run(source).returncode == 0


def test_multiple_interfaces(run):
    """
    'struct S: I, J' implements both, each checked.
    """
    source = """
    interface Named { name: char[]; }
    interface Aged { age: i32; }
    fn Aged::older(self: &Aged, than: i32) -> bool;

    struct Person: Named, Aged {
        name: char[];
        age: i32;
    }
    fn Person::older(&self, than: i32) -> bool { return self.age > than; }

    fn senior(a: Aged) -> bool { return a.older(64); }

    fn main() -> i32 {
        let p: Person = { "ada", 82 };
        return senior(p) ? 0 : 1;
    }
    """
    assert run(source).returncode == 0


def test_conformance_is_checked(compile_source):
    """
    A struct claiming an interface must declare its fields and provide
    its actions with matching signatures; a generic struct's instances
    check with their arguments substituted.
    """
    with pytest.raises(TypeError, match="struct 'P' does not implement "
                                        "'Named': it is missing the field"):
        compile_source("""
        interface Named { name: char[]; }
        struct P: Named { age: i32; }
        fn main() -> i32 { return 0; }
        """)

    with pytest.raises(TypeError, match="struct 'P' does not implement "
                                        "'Named': it is missing the method 'greet'"):
        compile_source("""
        interface Named { name: char[]; }
        fn Named::greet(self: &Named) -> i32;
        struct P: Named { name: char[]; }
        fn main() -> i32 { return 0; }
        """)

    with pytest.raises(TypeError, match="method 'greet' must return 'i32'"):
        compile_source("""
        interface Named { name: char[]; }
        fn Named::greet(self: &Named) -> i32;
        struct P: Named { name: char[]; }
        fn P::greet(&self) -> u8 { return 1; }
        fn main() -> i32 { return 0; }
        """)

    with pytest.raises(TypeError, match="struct 'Broken' does not implement "
                                        "'Iterator<i32>'"):
        compile_source("""
        interface Iterator<T>;
        fn Iterator<T>::next(self: &Iterator<T>, value: &T) -> bool;
        struct Broken<T>: Iterator<T> { x: T; }
        fn main() -> i32 { let b: Broken<i32>; return 0; }
        """)


def test_interface_misuse_is_rejected(compile_source):
    """
    Only a parameter can take an interface: a non-implementing argument,
    an interface-typed local, and an action with a body all error.
    """
    with pytest.raises(TypeError, match="type 'Plain' does not implement "
                                        "interface 'Named'"):
        compile_source("""
        interface Named { name: char[]; }
        struct Plain { x: i32; }
        fn f(n: Named) -> i32 { return 0; }
        fn main() -> i32 { let p: Plain = {1}; return f(p); }
        """)

    with pytest.raises(TypeError, match="interface 'Named' is not a "
                                        "concrete type"):
        compile_source("""
        interface Named { name: char[]; }
        fn main() -> i32 { let n: Named; return 0; }
        """)

    with pytest.raises(TypeError, match="an interface action cannot have a body"):
        compile_source("""
        interface Named { name: char[]; }
        fn Named::greet(self: &Named) -> i32 { return 1; }
        fn main() -> i32 { return 0; }
        """)
