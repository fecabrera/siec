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
