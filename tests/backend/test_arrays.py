"""Feature tests for arrays: the fat {data, length} representation."""


def test_length_written_and_read(run):
    """
    An array's '.length' can be written and read back.
    """
    source = """
    fn main() -> i32 {
        let a: i32[];
        a.length = 7;
        if (a.length == 7) {
            return 1;
        }
        return 0;
    }
    """
    assert run(source).returncode == 1


def test_array_passed_by_value(run):
    """
    An array passes to a function as the fat {ptr, length} struct.
    """
    source = """
    fn count(a: i32[]) -> u64 {
        return a.length;
    }

    fn main() -> i32 {
        let a: i32[];
        a.length = 5;
        if (count(a) == 5) {
            return 2;
        }
        return 0;
    }
    """
    assert run(source).returncode == 2


def test_aggregate_initialization_from_pointer_and_length(run):
    """
    A '{ptr, n}' literal builds an array from a data pointer and length; its
    data points at the given buffer.
    """
    source = """
    fn main(argc: i32, argv: char**) -> i32 {
        let name: char[] = {argv[0], 5};
        // the array's data points at argv[0], so their first bytes match
        if (name.length == 5 and name.data[0] == argv[0][0]) {
            return 3;
        }
        return 0;
    }
    """
    assert run(source).returncode == 3
