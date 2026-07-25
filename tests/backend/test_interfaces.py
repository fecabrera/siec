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
    interface Cursor<T>;

    fn Cursor<T>::next(self: &Cursor<T>, value: &T) -> bool;

    struct ArrayCursor<T>: Cursor<T> {
        arr: T[];
        index: u64;
    }

    fn ArrayCursor<T>::init(&self, arr: T[]) {
        self.arr = arr;
        self.index = 0;
    }

    fn ArrayCursor<T>::next(&self, value: &T) -> bool {
        if (self.index >= self.arr.length) {
            return false;
        }
        value = self.arr[self.index];
        self.index += 1;
        return true;
    }

    fn sum(it: Cursor<i32>) -> i32 {
        let total = 0;
        let v: i32;
        while (it.next(v)) {
            total += v;
        }
        return total;
    }

    fn main() -> i32 {
        let nums: i32[] = [10, 12, 20];
        let it = ArrayCursor<i32>(nums);
        return sum(it) - 42;
    }
    """
    assert run(source).returncode == 0


def test_builtin_iterator_interface(run):
    """
    'Iterator<T>' is builtin: 'has_next' and 'next() -> &T' required of
    implementers, no declaration or import needed, and 'next' aliases
    the underlying storage like any reference return.
    """
    source = """
    struct StepIter<T>: Iterator<T> {
        arr: T[];
        index: u64;
    }

    fn StepIter<T>::init(&self, arr: T[]) {
        self.arr = arr;
        self.index = 0;
    }

    fn StepIter<T>::has_next(&self) -> bool {
        return self.index < self.arr.length;
    }

    fn StepIter<T>::next(&self) -> &T {
        self.index += 1;
        return self.arr[self.index - 1];
    }

    fn sum(it: Iterator<i32>) -> i32 {
        let total = 0;
        while (it.has_next()) {
            total += it.next();
        }
        return total;
    }

    fn main() -> i32 {
        let nums: i32[] = [10, 12, 20];
        let it = StepIter<i32>(nums);
        if (sum(it) != 42) { return 1; }

        let again = StepIter<i32>(nums);
        again.next() = 5;                    // the reference assigns through
        if (nums[0] != 5) { return 2; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_builtin_iterable_interface(run):
    """
    'Iterable<T>' is builtin: 'iterator() -> Iterator<T>' satisfied by
    any method returning an implementing type, and an 'Iterable<i32>'
    parameter walks the chain.
    """
    source = """
    struct StepIter<T>: Iterator<T> {
        arr: T[];
        index: u64;
    }

    fn StepIter<T>::init(&self, arr: T[]) {
        self.arr = arr;
        self.index = 0;
    }

    fn StepIter<T>::has_next(&self) -> bool {
        return self.index < self.arr.length;
    }

    fn StepIter<T>::next(&self) -> &T {
        self.index += 1;
        return self.arr[self.index - 1];
    }

    struct List<T>: Iterable<T> {
        data: T*;
        length: u64;
    }

    fn List<T>::iterator(&self) -> StepIter<T> {
        return StepIter<T>({self.data, self.length});
    }

    fn List<T>::const_iterator(const &self) -> ConstArrayIterator<T> {
        let it: ConstArrayIterator<T> = { {self.data, self.length}, 0 };
        return it;
    }

    fn total(coll: Iterable<i32>) -> i32 {
        let it = coll.iterator();
        let sum = 0;
        while (it.has_next()) {
            sum += it.next();
        }
        return sum;
    }

    fn main() -> i32 {
        let nums: i32[] = [10, 12, 20];
        let l: List<i32> = { nums.data, nums.length };
        return total(l) - 42;
    }
    """
    assert run(source).returncode == 0


def test_arrays_are_iterable(run):
    """
    'T[]' implements 'Iterable<T>' through the builtin ArrayIterator<T>:
    an array passes to an Iterable parameter, answers 'iterator()'
    directly, and 'next()' references the array's own elements.
    """
    source = """
    fn total(coll: Iterable<i32>) -> i32 {
        let it = coll.iterator();
        let sum = 0;
        while (it.has_next()) {
            sum += it.next();
        }
        return sum;
    }

    fn main() -> i32 {
        let nums: i32[] = [10, 12, 20];
        if (total(nums) != 42) { return 1; }

        let it = nums.iterator();
        if (it.next() != 10) { return 2; }

        it.next() = 99;                     // writes through to the array
        if (nums[1] != 99) { return 3; }

        let direct = ArrayIterator<i32>(nums);
        return direct.has_next() ? 0 : 4;
    }
    """
    assert run(source).returncode == 0


def test_iterable_requires_an_iterator_return(compile_source):
    """
    'iterator' must return a type implementing 'Iterator<T>'.
    """
    with pytest.raises(TypeError, match="method 'iterator' must return "
                                        "'Iterator<i32>'"):
        compile_source("""
        struct NotIter<T> { x: T; }
        struct L<T>: Iterable<T> { x: T; }
        fn L<T>::iterator(&self) -> NotIter<T> { let n: NotIter<T>; return n; }
        fn main() -> i32 { let l: L<i32>; return 0; }
        """)


def test_builtin_iterator_cannot_be_redeclared(compile_source):
    """
    'Iterator' is builtin: a user interface under the name collides.
    """
    with pytest.raises(TypeError, match="interface 'Iterator' is declared "
                                        "more than once"):
        compile_source("""
        interface Iterator<T>;
        fn main() -> i32 { return 0; }
        """)


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
                                        r"'Named': it is missing the method 'greet\(\) -> i32'"):
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


def test_interface_body_declares_actions(run):
    """
    An interface's actions may sit in its body: each 'fn' signature
    spells the 'fn I::m(...)' it means, '&self' naming the interface.
    """
    source = """
    interface Doubler {
        scale: i32;
        fn double(&self, v: i32) -> i32;
    }

    struct Two: Doubler { scale: i32; }
    fn Two::double(&self, v: i32) -> i32 { return v * self.scale; }

    fn apply(d: Doubler, v: i32) -> i32 { return d.double(v); }

    fn main() -> i32 {
        let two: Two;
        two.scale = 2;
        return apply(two, 21) - 42;
    }
    """
    assert run(source).returncode == 0


def test_interface_body_actions_overload(run):
    """
    A body may declare a name more than once, each signature its own
    requirement, satisfied by the implementer's matching overloads.
    """
    source = """
    interface Reader {
        fn read(&self, buf: &u8[], count: u64) -> i64;
        fn read(&self, buf: &u8[]) -> i64;
    }

    struct Mem: Reader { fill: u8; }

    fn Mem::read(&self, buf: &u8[], count: u64) -> i64 {
        for (let i: u64 = 0; i < count; i += 1) { buf[i] = self.fill; }
        return count as i64;
    }

    fn Mem::read(&self, buf: &u8[]) -> i64 {
        return self.read(buf, buf.length);
    }

    fn drain(src: Reader, buf: &u8[]) -> i64 { return src.read(buf); }

    fn main() -> i32 {
        let m: Mem;
        m.fill = 7;

        let backing: u8[4];
        if (drain(m, backing) != 4) { return 1; }
        return backing[3] as i32 - 7;
    }
    """
    assert run(source).returncode == 0


def test_interface_body_requires_every_overload(compile_source):
    """
    An implementer missing one of an overloaded action's signatures does
    not conform.
    """
    with pytest.raises(TypeError, match="does not implement 'Reader'"):
        compile_source("""
        interface Reader {
            fn read(&self, buf: &u8[], count: u64) -> i64;
            fn read(&self, buf: &u8[]) -> i64;
        }

        struct Mem: Reader { fill: u8; }
        fn Mem::read(&self, buf: &u8[]) -> i64 { return 0; }

        fn main() -> i32 { return 0; }
        """)


def test_generic_interface_body(run):
    """
    A generic interface's body speaks its type parameters, '&self'
    carrying them.
    """
    source = """
    interface Producer<T> {
        fn produce(&self) -> T;
    }

    struct Five: Producer<i64> {}
    fn Five::produce(&self) -> i64 { return 5; }

    fn take(p: Producer<i64>) -> i64 { return p.produce(); }

    fn main() -> i32 {
        let five: Five;
        return (take(five) - 5) as i32;
    }
    """
    assert run(source).returncode == 0


def test_struct_body_rejects_methods(compile_source):
    """
    Only interfaces declare actions in their bodies; a struct's methods
    are declared outside it.
    """
    with pytest.raises(SyntaxError, match="declared outside its body"):
        compile_source("""
        struct S {
            fn get(&self) -> i32;
        }
        fn main() -> i32 { return 0; }
        """)


def test_interface_body_rejects_respelled_actions(compile_source):
    """
    The same signature twice is a redeclaration, not an overload.
    """
    with pytest.raises(TypeError, match="declared more than once"):
        compile_source("""
        interface Reader {
            fn read(&self, buf: &u8[]) -> i64;
            fn read(&self, buf: &u8[]) -> i64;
        }
        fn main() -> i32 { return 0; }
        """)


def test_interface_functions_overload_on_value_params(run):
    """
    Two functions may share a name and an interface-typed parameter,
    their other parameters telling the calls apart - print's shape.
    """
    source = """
    interface Out {
        fn put(&self, v: i32);
    }

    struct Acc: Out { total: i32; }
    fn Acc::put(&self, v: i32) { self.total += v; }

    fn send(out: &Out, v: i32) { out.put(v); }
    fn send(out: &Out, v: const char[]) { out.put(v.length as i32); }

    fn main() -> i32 {
        let acc: Acc;
        acc.total = 0;
        send(acc, 40);
        send(acc, "ab");
        return acc.total - 42;
    }
    """
    assert run(source).returncode == 0


def test_arrays_are_iterable_by_definition(run):
    """
    'T[]' implements 'Iterable<T>' through the prelude's family claim:
    an array passes where the interface is expected, answers
    '.iterator()' itself, and 'Iterable<T>[]' binds the whole array,
    its free T unifying with the element.
    """
    source = """
    fn total(values: const Iterable<i32>) -> i32 {
        let sum = 0;
        foreach (v : values) {
            sum += v;
        }
        return sum;
    }

    fn count(values: const Iterable<T>[]) -> u64 {
        return values.length;
    }

    fn main() -> i32 {
        let arr: i32[] = [13, 12, 14];

        let it = arr.iterator();
        let first = 0;
        if (it.has_next()) {
            first = it.next();
        }

        return total(arr) + first as i32 + count(arr) as i32 - 55;
    }
    """
    assert run(source).returncode == 0


def test_a_failed_constraint_names_the_whole_argument(compile_source):
    """
    A constraint failure blames the argument as passed - the array, not
    the element the placeholder sat next to.
    """
    with pytest.raises(TypeError, match="type 'P' does not implement "
                                        "interface 'Iterable<char>'"):
        compile_source("""
        struct P { x: i32; }

        fn takes(it: const Iterable<char>) -> u64 { return 0; }

        fn main() -> i32 {
            let p: P;
            takes(p);
            return 0;
        }
        """)


def test_a_static_method_adapts_its_first_interface_parameter(run):
    """
    Only a method's '&self' is exempt from interface adaptation: a
    static method's first parameter takes an interface like any other.
    """
    source = """
    interface Named;

    fn Named::name(const &self) -> const char[];

    struct P: Named { tag: char[]; }
    fn P::name(const &self) -> const char[] { return self.tag; }

    struct Holder { total: u64; }

    fn Holder::measure(n: Named) -> u64 {
        return n.name().length;
    }

    fn main() -> i32 {
        let p: P = { "abcd" };
        return Holder::measure(p) as i32 - 4;
    }
    """
    assert run(source).returncode == 0


def test_a_claim_may_take_an_interface_argument(run):
    """
    An interface claim's type argument may itself be an interface -
    'Add<Box, Iterable<i32>>' - the requirement satisfied by the
    interface-taking overload, template though it is.
    """
    source = """
    struct Box: Add<Box, Iterable<i32>> {
        total: i32;
    }

    fn Box::add(&self, it: const &Iterable<i32>) -> Box {
        let sum = self.total;
        foreach (v : it) {
            sum += v;
        }
        let b: Box = { sum };
        return b;
    }

    fn main() -> i32 {
        let b: Box = { 5 };
        let arr: i32[] = [1, 2, 3];
        let c = b.add(arr);
        return c.total - 11;
    }
    """
    assert run(source).returncode == 0


def test_a_literal_takes_the_array_reading_of_a_constraint(run):
    """
    An aggregate literal has no type of its own; against an
    'Iterable<T>'-constrained parameter it reads as the 'T[]' the array
    family claims - '{ptr, len}' where an 'Iterable<i32>' is expected.
    """
    source = """
    fn total(values: const &Iterable<i32>) -> i32 {
        let sum = 0;
        foreach (v : values) {
            sum += v;
        }
        return sum;
    }

    fn main() -> i32 {
        let arr: i32[] = [13, 12, 14, 99];
        return total({arr.data, 3}) - 39;
    }
    """
    assert run(source).returncode == 0
