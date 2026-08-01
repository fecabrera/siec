"""Feature tests for the builtin 'Any' type and '@typeof'."""

import pytest


def test_any_wraps_and_typeof_dispatches(run):
    """
    'v as Any' pairs a type id with the value's address; '@typeof' reads
    an Any's runtime id or folds a static type's; '== T' and 'when T:'
    sugar mean the type's id; 'a as T' reads the value back.
    """
    source = """
    struct List<T> { data: T*; length: u64; }
    @type String = List<char>;

    fn count_kinds(args: Any[]) -> Tuple<i32, i32, i32> {
        let (chars, strs, others) = (0, 0, 0);
        foreach (arg : args) {
            case (@typeof(arg)) {
                when char[]: chars += 1;
                when String: strs += 1;
                else: others += 1;
            }
        }
        return (chars, strs, others);
    }

    fn main() -> i32 {
        let num: u64 = 42;
        let arg = num as Any;

        if (arg.id != @typeid(u64)) { return 1; }
        if (@typeof(arg) != @typeid(u64)) { return 2; }
        if (@typeof(arg) != u64) { return 3; }       // the '== T' sugar
        if (@typeof(arg) == f32) { return 4; }

        if ((arg as u64) != 42) { return 5; }        // unwrap

        let s: String;
        if (@typeof(s) != String) { return 6; }      // folds at compile time
        if (@typeof(s) != List<char>) { return 7; }  // to the alias's target

        // one Any[] holds heterogeneous values; one function takes them
        let text: char[] = "hi";
        let args: Any[] = [num as Any, text as Any, s as Any, 1.5 as Any];
        let (chars, strs, others) = count_kinds(args);
        if (chars != 1 or strs != 1 or others != 2) { return 8; }

        let n: i32 = 7;                              // wraps copy at wrap time
        let a = n as Any;
        n = 9;
        return (a as i32) - 7;
    }
    """
    assert run(source).returncode == 0


def test_any_is_one_concrete_type(run):
    """
    'Any' is a single struct, so wrapping twice is the same value and
    functions over Any never stamp per payload.
    """
    source = """
    fn ident(a: Any) -> u64 { return @typeof(a); }

    fn main() -> i32 {
        let x: i32 = 1;
        let once = x as Any;
        let twice = (x as Any) as Any;
        if (@typeof(once) != @typeof(twice)) { return 1; }

        if (ident(x as Any) != @typeid(i32)) { return 2; }
        if (ident(1.5 as Any) != @typeid(f64)) { return 3; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_any_const_view_borrows_owned_value(run):
    """An erased owned value can be inspected without gaining another owner."""
    source = """
    @static let drops: i32 = 0;

    struct Resource: Destroy { value: i32; }
    fn Resource::destroy(&self) { drops += 1; }

    fn read(args...) -> i32 {
        let resource = args[0] as const Resource;
        return resource.value;
    }

    fn exercise() -> i32 {
        let resource: Resource = { 42 };
        if (read(resource) != 42) return 1;
        return drops;
    }

    fn main() -> i32 {
        if (exercise() != 0) return 1;
        return drops - 1;
    }
    """
    assert run(source).returncode == 0


def test_when_interface_expands_per_implementer(run):
    """
    A 'when Iface:' arm of a '@typeof' case is a generic arm: one arm
    per implementing type, the body stamped with the concrete type
    wherever the interface is spelled, so the cast reads the arm's own
    type.
    """
    source = """
    interface Doubler;

    fn Doubler::doubled(const &self) -> i64;

    struct P: Doubler { x: i64; }
    fn P::doubled(const &self) -> i64 { return self.x * 2; }

    struct Q: Doubler { y: i64; }
    fn Q::doubled(const &self) -> i64 { return self.y * 3; }

    fn tally(args...) -> i64 {
        let total: i64 = 0;
        let i: u64 = 0;
        while (i < args.length) {
            case (@typeof(args[i])) {
            when i64:
                total = total + (args[i] as i64);
            when Doubler:
                let v = args[i] as Doubler;
                total = total + v.doubled();
            else:
                total = total + 1000;
            }
            i = i + 1;
        }
        return total;
    }

    fn main() -> i32 {
        let p: P = { 10 };
        let q: Q = { 7 };
        let n: i64 = 4;
        return (tally(n, p, q, false) - 1045) as i32;
    }
    """
    assert run(source).returncode == 0


def test_when_interface_covers_the_array_family(run):
    """
    The family's claim names the array: 'when Iterable<char>:' arms
    'char[]' among the implementers, the cast reading 'char[]'.
    """
    source = """
    fn width(args...) -> u64 {
        case (@typeof(args[0])) {
        when Iterable<char>:
            let arg = args[0] as Iterable<char>;
            return arg.length;
        }
        return 100;
    }

    fn main() -> i32 {
        return width("hello") as i32 - 5;
    }
    """
    assert run(source).returncode == 0


def test_when_interface_covers_a_bounded_array_claim(run):
    """
    A family whose element appears only in its bound still contributes
    each known matching array to an interface arm.
    """
    source = """
    interface Formattable {
        fn format(const &self) -> i32;
    }

    @extend i32: Formattable {
        fn format(const &self) -> i32 { return self; }
    }

    @extend char: Formattable {
        fn format(const &self) -> i32 { return self as i32; }
    }

    @template<T: Formattable>
    @extend T[]: Formattable {
        fn format(const &self) -> i32 { return self.length as i32; }
    }

    @override
    fn char[]::format(const &self) -> i32 {
        return self.length as i32;
    }

    fn format_one(args...) -> i32 {
        case (@typeof(args[0])) {
        when Formattable:
            let value = args[0] as Formattable;
            return value.format();
        }
        return 100;
    }

    fn main() -> i32 {
        return (format_one([1, 2, 3] as i32[])
                + format_one(["a", "b"] as char[][])) - 5;
    }
    """
    assert run(source).returncode == 0


def test_when_interface_expands_nested_combinations(run):
    """
    A nested interface argument expands per combination:
    'when Iterable<Sized>:' arms every array whose element implements
    'Sized', the cast reading each arm's own array type.
    """
    source = """
    interface Sized;

    fn Sized::size(const &self) -> i64;

    struct P: Sized { x: i64; }
    fn P::size(const &self) -> i64 { return self.x; }

    @extend i64: Sized;
    fn i64::size(const &self) -> i64 { return self; }

    fn tally(args...) -> i64 {
        let total: i64 = 0;
        let i: u64 = 0;
        while (i < args.length) {
            case (@typeof(args[i])) {
            when Sized:
                let v = args[i] as Sized;
                total = total + v.size();
            when Iterable<Sized>:
                let v = args[i] as Iterable<Sized>;
                foreach (el : v) {
                    total = total + el.size();
                }
            else:
                total = total + 1000;
            }
            i = i + 1;
        }
        return total;
    }

    fn main() -> i32 {
        let p: P = { 3 };
        let nums: i64[] = [10, 20];
        let ps: P[] = [{ 1 }, { 2 }];
        let f = 1.5;
        return (tally(p, nums, ps, f) - 1036) as i32;
    }
    """
    assert run(source).returncode == 0
