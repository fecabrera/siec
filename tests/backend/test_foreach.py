"""Feature tests for 'foreach (v : iterable)' loops."""

import pytest


def test_foreach_walks_iterables(run):
    """
    'foreach' takes an array, a struct Iterable, or an iterator value,
    and 'v' is a true reference: writing it reaches the collection.
    """
    source = """
    struct List<T>: Iterable<T> {
        data: T*;
        length: u64;
    }

    fn List<T>::iterator(&self) -> ArrayIterator<T> {
        return ArrayIterator<T>({self.data, self.length});
    }

    fn main() -> i32 {
        let nums: i32[] = [10, 12, 20];

        let sum = 0;
        foreach (v : nums) {
            sum += v;
        }
        if (sum != 42) { return 1; }

        foreach (v : nums) {
            v = v * 2;                  // a true reference: writes through
        }
        if (nums[0] != 20 or nums[2] != 40) { return 2; }

        let l: List<i32> = { nums.data, nums.length };
        let total = 0;
        foreach (v : l) {
            total += v;
        }
        if (total != 84) { return 3; }

        let it = nums.iterator();       // an iterator iterates itself
        it.next();
        let rest = 0;
        foreach (v : it) {
            rest += v;
        }
        if (rest != 64) { return 4; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_foreach_nests_and_steers(run):
    """
    'foreach' loops nest, and 'break'/'continue' steer like any loop.
    """
    source = """
    fn main() -> i32 {
        let rows: i32[] = [1, 2];
        let cols: i32[] = [10, 30];

        let sum = 0;
        foreach (r : rows) {
            foreach (c : cols) {
                if (c == 30 and r == 2) { continue; }
                sum += r * c;
            }
        }
        if (sum != 60) { return 1; }    // 10+30+20

        let firsts = 0;
        foreach (r : rows) {
            firsts += r;
            break;
        }
        return firsts - 1;
    }
    """
    assert run(source).returncode == 0


def test_foreach_over_const_arrays(run, compile_source):
    """
    A const array iterates through ConstArrayIterator: elements read as
    'const &T', so writing one is an error.
    """
    source = """
    fn total(arr: const &i32[]) -> i32 {
        let sum = 0;
        foreach (v : arr) {
            sum += v;
        }
        return sum;
    }

    fn main() -> i32 {
        let nums: i32[] = [10, 12, 20];
        return total(nums) - 42;
    }
    """
    assert run(source).returncode == 0

    with pytest.raises(TypeError, match="cannot assign to const variable 'v'"):
        compile_source("""
        fn tamper(arr: const &i32[]) {
            foreach (v : arr) { v = 0; }
        }
        fn main() -> i32 { return 0; }
        """)


def test_foreach_rejects_non_iterables(compile_source):
    """
    A value that is neither an Iterable nor an Iterator cannot be walked.
    """
    with pytest.raises(TypeError, match="cannot iterate a 'i32' value"):
        compile_source("""
        fn main() -> i32 {
            let n = 5;
            foreach (v : n) { }
            return 0;
        }
        """)
