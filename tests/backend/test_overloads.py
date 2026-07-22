"""Feature tests for function overloading: one name, distinct signatures."""

import pytest


def test_overloads_pick_by_argument_type(run):
    """
    Each call resolves to the overload its argument's declared type names.
    """
    source = """
    fn describe(n: i64) -> i32 { return 1; }
    fn describe(f: f64) -> i32 { return 2; }
    fn describe(s: char*) -> i32 { return 3; }

    fn main() -> i32 {
        let n: i64 = 0;
        let f: f64 = 0.0;
        let s: char* = "x";
        return describe(n) * 100 + describe(f) * 10 + describe(s); // 123
    }
    """
    assert run(source).returncode == 123


def test_exact_match_beats_widening(run):
    """
    An 'i32' argument picks the i32 overload even though it also widens to i64.
    """
    source = """
    fn pick(n: i32) -> i32 { return 1; }
    fn pick(n: i64) -> i32 { return 2; }

    fn main() -> i32 {
        let narrow: i32 = 0;
        let wide: i64 = 0;
        return pick(narrow) * 10 + pick(wide); // 12
    }
    """
    assert run(source).returncode == 12


def test_untyped_literal_ranks_at_its_default(run):
    """
    An integer literal ranks as i32 - exact into an i32 overload, widening
    into i64 - and a literal too big for i32 ranks as i64 directly.
    """
    source = """
    fn pick(n: i32) -> i32 { return 1; }
    fn pick(n: i64) -> i32 { return 2; }

    fn main() -> i32 {
        return pick(5) * 10 + pick(5000000000); // 12
    }
    """
    assert run(source).returncode == 12


def test_literal_widens_when_no_exact_overload_exists(run):
    """
    'dec.add(5)': with no i32 candidate, the literal's i32 widens to i64,
    never crossing into the unsigned or float candidates.
    """
    source = """
    struct Decimal { value: i64; }

    fn Decimal::add(&self, d: const &Decimal) -> i32 { return 1; }
    fn Decimal::add(&self, n: i64) -> i32 { return 2; }
    fn Decimal::add(&self, f: f64) -> i32 { return 3; }

    fn main() -> i32 {
        let dec: Decimal = {0};
        return dec.add(5);
    }
    """
    assert run(source).returncode == 2


def test_methods_overload_on_the_receiver_type(run):
    """
    Method overloads resolve like free functions, the receiver joining
    the arguments; a struct argument picks the reference candidate.
    """
    source = """
    struct Decimal { value: i64; }

    fn Decimal::add(&self, d: const &Decimal) -> i64 { return self.value + d.value; }
    fn Decimal::add(&self, n: i64) -> i64 { return self.value + n; }

    fn main() -> i32 {
        let a: Decimal = {40};
        let b: Decimal = {2};
        return (a.add(b) + a.add(0)) as i32 - 40; // 42 + 40 - 40
    }
    """
    assert run(source).returncode == 42


def test_unsigned_arguments_stay_in_their_prefix(run):
    """
    A u8 argument widens into the u64 overload, never the i64 one.
    """
    source = """
    fn pick(n: i64) -> i32 { return 1; }
    fn pick(n: u64) -> i32 { return 2; }

    fn main() -> i32 {
        let n: u8 = 5;
        return pick(n);
    }
    """
    assert run(source).returncode == 2


def test_overloads_pick_by_arity(run):
    """
    Overloads may differ in parameter count alone.
    """
    source = """
    fn pick() -> i32 { return 1; }
    fn pick(n: i32) -> i32 { return 2; }

    fn main() -> i32 {
        return pick() * 10 + pick(0); // 12
    }
    """
    assert run(source).returncode == 12


def test_return_types_may_differ_across_overloads(run):
    """
    Inference follows the picked overload's return type.
    """
    source = """
    fn half(n: i64) -> i64 { return n / 2; }
    fn half(f: f64) -> f64 { return f / 2.0; }

    fn main() -> i32 {
        let n = half(84 as i64);   // i64
        let f = half(85.0);        // f64
        return (n + f as i64) as i32 - 42; // 42 + 42 - 42
    }
    """
    assert run(source).returncode == 42


def test_ambiguous_conversions_are_an_error(compile_source):
    """
    An argument widening into two candidates alike has no winner.
    """
    with pytest.raises(TypeError, match="ambiguous"):
        compile_source("""
        fn pick(n: i16) -> i32 { return 1; }
        fn pick(n: i64) -> i32 { return 2; }

        fn main() -> i32 {
            let n: i8 = 0;
            return pick(n);
        }
        """)


def test_no_matching_overload_is_an_error(compile_source):
    """
    An argument no candidate takes names the types it offered.
    """
    with pytest.raises(TypeError, match="no overload of 'pick' takes"):
        compile_source("""
        fn pick(n: i64) -> i32 { return 1; }
        fn pick(f: f64) -> i32 { return 2; }

        fn main() -> i32 {
            return pick(true);
        }
        """)


def test_same_signature_twice_is_still_a_conflict(compile_source):
    """
    Overloading needs distinct parameter lists; repeating one is the same
    redefinition error as ever.
    """
    with pytest.raises(TypeError, match="defined more than once"):
        compile_source("""
        fn f(n: i32) -> i32 { return 1; }
        fn f(n: i32) -> i32 { return 2; }

        fn main() -> i32 { return 0; }
        """)


def test_return_type_alone_cannot_overload(compile_source):
    """
    Two signatures differing only in return type give calls nothing to
    pick by.
    """
    with pytest.raises(TypeError, match="conflicting declarations"):
        compile_source("""
        fn f(n: i32) -> i32 { return 1; }
        fn f(n: i32) -> i64 { return 2; }

        fn main() -> i32 { return 0; }
        """)


def test_extern_functions_cannot_overload(compile_source):
    """
    An '@extern' function names one foreign symbol.
    """
    with pytest.raises(TypeError, match="cannot overload '@extern'"):
        compile_source("""
        fn f(n: i32) -> i32 { return 1; }
        @extern fn f(s: char*) -> i32;

        fn main() -> i32 { return 0; }
        """)


def test_bare_reference_to_an_overloaded_name_is_ambiguous(compile_source):
    """
    A bare reference has no arguments to pick a candidate by.
    """
    with pytest.raises(TypeError, match="ambiguous reference"):
        compile_source("""
        fn f(n: i32) -> i32 { return 1; }
        fn f(n: i64) -> i32 { return 2; }

        fn main() -> i32 {
            let g = f;
            return 0;
        }
        """)


def test_generic_struct_methods_overload(run):
    """
    A generic struct's method overloads like any other, its templates
    stamped together per instantiation.
    """
    source = """
    struct Box<T> {
        total: i64;
    }

    fn Box<T>::put(&self, v: T) {
        self.total += v as i64;
    }

    fn Box<T>::put(&self, arr: const T*, n: u64) {
        for (let i: u64 = 0; i < n; i += 1) {
            self.total += arr[i] as i64;
        }
    }

    fn main() -> i32 {
        let b: Box<i32> = {0};
        let nums: i32[] = [10, 12];

        b.put(20);
        b.put(nums.data, nums.length);
        return b.total as i32; // 20 + 22
    }
    """
    assert run(source).returncode == 42


def test_concrete_overload_beside_an_interface_one(run):
    """
    A concrete overload coexists with one taking an interface parameter:
    a fitting argument picks the concrete, anything else falls through
    to the interface template.
    """
    source = """
    struct StepIter<T>: Iterator<T> {
        arr: T[];
        index: u64;
    }

    fn StepIter<T>::has_next(&self) -> bool {
        return self.index < self.arr.length;
    }

    fn StepIter<T>::next(&self) -> &T {
        self.index += 1;
        return self.arr[self.index - 1];
    }

    struct List<T>: Iterable<T> {
        total: i64;
    }

    fn List<T>::iterator(&self) -> StepIter<T> {
        let empty: T[] = [];
        let it: StepIter<T> = {empty, 0};
        return it;
    }

    fn List<T>::append(&self, arr: const T*, n: u64) {
        for (let i: u64 = 0; i < n; i += 1) {
            self.total += arr[i] as i64;
        }
    }

    fn List<T>::append(&self, arr: const Iterable<T>) {
        foreach (el : arr) {
            self.total += el as i64;
        }
    }

    fn main() -> i32 {
        let l: List<i32> = {0};
        let nums: i32[] = [10, 11];

        l.append(nums.data, nums.length);  // the pointer overload
        l.append(nums);                    // the Iterable overload
        return l.total as i32; // 21 + 21
    }
    """
    assert run(source).returncode == 42


def test_constructor_picks_among_init_overloads(run):
    """
    'S(...)' resolves an overloaded 'init' like any call, the instance
    standing in as the receiver; arguments no concrete candidate takes
    instantiate a generic 'init' (an interface parameter's, say).
    """
    source = """
    struct Box<T>: Iterable<T> {
        value: i64;
    }

    struct BoxIter<T>: Iterator<T> {
        arr: T[];
        index: u64;
    }

    fn BoxIter<T>::has_next(&self) -> bool {
        return self.index < self.arr.length;
    }

    fn BoxIter<T>::next(&self) -> &T {
        self.index += 1;
        return self.arr[self.index - 1];
    }

    fn Box<T>::iterator(&self) -> BoxIter<T> {
        let empty: T[] = [];
        let it: BoxIter<T> = {empty, 0};
        return it;
    }

    fn Box<T>::init(&self, start: i64 = 5) {
        self.value = start;
    }

    fn Box<T>::init(&self, arr: Iterable<T>) {
        self.value = 0;
        foreach (el : arr) {
            self.value += el as i64;
        }
    }

    fn main() -> i32 {
        let a = Box<i32>();          // the defaulted overload: 5
        let b = Box<i32>(7);         // the same, explicit: 7
        let nums: i32[] = [10, 20];
        let c = Box<i32>(nums);      // the generic Iterable overload: 30

        return (a.value + b.value + c.value) as i32; // 42
    }
    """
    assert run(source).returncode == 42


def test_string_literal_fills_a_char_array_overload(run):
    """
    A string literal ranks as 'char*' but also fills a 'char[]'
    candidate, the fat value it already is.
    """
    source = """
    fn measure(s: const char[]) -> i32 { return s.length as i32; }
    fn measure(n: u64) -> i32 { return -1; }

    fn main() -> i32 {
        return measure("*") + 41;
    }
    """
    assert run(source).returncode == 42


def test_aggregate_literals_fit_by_shape(run):
    """
    An aggregate literal only fits a struct or array parameter with as
    many fields, so its shape picks the candidate.
    """
    source = """
    struct Pair { a: i32; b: i32; }

    fn pick(p: Pair) -> i32 { return p.a + p.b; }
    fn pick(n: u64) -> i32 { return -1; }

    fn main() -> i32 {
        return pick({40, 2});
    }
    """
    assert run(source).returncode == 42


def test_generic_struct_method_repeated_signature_is_an_error(compile_source):
    """
    A generic struct's method still rejects one signature declared twice.
    """
    with pytest.raises(TypeError, match="declared more than once"):
        compile_source("""
        struct Box<T> { x: T; }

        fn Box<T>::put(&self, v: T) {}
        fn Box<T>::put(&self, v: T) {}

        fn main() -> i32 { return 0; }
        """)


def test_unpicked_overload_bodies_never_emit(run):
    """
    A stamped overload fitting only some element types (walking a
    terminator, say) stays a bodiless declaration where no call picks it.
    """
    source = """
    struct Pair { x: i32; }

    struct Box<T> { count: u64; }

    fn Box<T>::put(&self, v: T) {
        self.count += 1;
    }

    fn Box<T>::put(&self, arr: const T*) {
        for (let i: u64 = 0; arr[i]; i += 1) {
            self.put(arr[i]);
        }
    }

    fn main() -> i32 {
        let b: Box<Pair> = {0};
        let p: Pair = {1};
        b.put(p);   // the value overload; the pointer walk never emits
        return b.count as i32 + 41;
    }
    """
    assert run(source).returncode == 42


def test_forward_declared_overloads_define_later(run):
    """
    Each forward declaration pairs with the definition sharing its
    signature, whichever order they appear in.
    """
    source = """
    fn pick(n: i64) -> i32;
    fn pick(f: f64) -> i32;

    fn main() -> i32 {
        let n: i64 = 0;
        return pick(n) * 10 + pick(0.0); // 12
    }

    fn pick(n: i64) -> i32 { return 1; }
    fn pick(f: f64) -> i32 { return 2; }
    """
    assert run(source).returncode == 12
