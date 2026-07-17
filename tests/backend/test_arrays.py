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


def test_array_literal_builds_the_fat_array(run):
    """
    A '[a, b, c]' literal stores its elements into a backing array, whose
    data and length read back through '.data' and '.length'.
    """
    source = """
    fn main() -> i32 {
        let ops: i32[] = [10, 20, 30];
        if (ops.length == 3 and ops.data[0] == 10
                and ops.data[1] == 20 and ops.data[2] == 30) {
            return 4;
        }
        return 0;
    }
    """
    assert run(source).returncode == 4


def test_array_literal_element_widens_to_the_declared_type(run):
    """
    An array literal's elements widen to the array's declared element type,
    the same as any other typed initializer.
    """
    source = """
    fn main() -> i32 {
        let ops: i64[] = [1, 2, 3];
        if (ops.data[2] == 3) {
            return 5;
        }
        return 0;
    }
    """
    assert run(source).returncode == 5


def test_string_literal_fills_a_char_array(run):
    """
    A string literal initializes a 'char[]', its length excluding the null.
    """
    source = """
    fn main() -> i32 {
        let msg: char[] = "hello";
        // 'h' is 104; the length counts the five letters, not the null
        if (msg.length == 5 and msg.data[0] == 104) {
            return 6;
        }
        return 0;
    }
    """
    assert run(source).returncode == 6


def test_array_literal_of_string_pointers(run):
    """
    A 'char*[]' literal holds each string as a plain pointer element.
    """
    source = """
    fn main() -> i32 {
        let cmds: char*[] = ["ls", "cd", "cp"];
        // 'c' is 99: the second command's first letter
        if (cmds.length == 3 and cmds.data[1][0] == 99) {
            return 7;
        }
        return 0;
    }
    """
    assert run(source).returncode == 7


def test_array_decays_at_a_pointer_parameter(run):
    """
    An array passed where a plain pointer is expected lowers to its data pointer.
    """
    source = """
    fn second(values: i32*) -> i32 {
        return values[1];
    }

    fn main() -> i32 {
        let arr: i32[] = [1, 2, 3];
        return second(arr);
    }
    """
    assert run(source).returncode == 2


def test_array_casts_to_its_element_pointer(run):
    """
    An 'arr as X*' cast yields the array's data pointer.
    """
    source = """
    fn main() -> i32 {
        let arr: i32[] = [1, 2, 3];
        let ptr: i32* = arr as i32*;
        return ptr[2];
    }
    """
    assert run(source).returncode == 3


def test_slices_view_the_backing_data(run):
    """
    'arr[from:to]' yields a view with the defaulted bounds applied; writing
    through the view changes the base array.
    """
    source = """
    fn main() -> i32 {
        let arr: i32[] = [1, 2, 3, 4, 5];

        let tail: i32[] = arr[1:];  // [2, 3, 4, 5]
        let head: i32[] = arr[:3];  // [1, 2, 3]
        let mid: i32[] = arr[1:3];  // [2, 3]

        tail.data[0] = 20; // writes through to arr.data[1]

        if (tail.length == 4 and head.length == 3 and mid.length == 2
                and mid.data[1] == 3 and arr.data[1] == 20) {
            return 9;
        }
        return 0;
    }
    """
    assert run(source).returncode == 9


def test_arrays_index_directly(run):
    """
    'arr[i]' reads and writes the backing data, equivalent to 'arr.data[i]';
    a literal indexes in place given an element context.
    """
    source = """
    fn main(argc: i32, argv: char**) -> i32 {
        let arr: i32[] = [10, 20, 30];
        arr[1] = 22;

        let prog: char* = {argv, argc as u64}[0];
        let inline: i32 = [7, 8, 9][2];

        if (arr[1] == 22 and arr.data[1] == 22 and prog[0] != 0 and inline == 9) {
            return 6;
        }
        return 0;
    }
    """
    assert run(source).returncode == 6


def test_aggregate_literal_slices_in_place(run):
    """
    A '{ptr, n}' literal can be sliced directly, taking its shape from the
    declaration it initializes: the argv-skipping idiom.
    """
    source = """
    fn main(argc: i32, argv: char**) -> i32 {
        let args: char*[] = {argv, argc as u64}[1:];
        return args.length as i32;
    }
    """
    assert run(source, "a", "b").returncode == 2


def test_pointers_and_arrays_decay_to_opaque(run):
    """
    Typed pointers and arrays pass to 'opaque*' parameters with no cast.
    """
    source = """
    @extern fn memcpy(dest: opaque*, src: opaque*, count: u32) -> opaque*;

    fn main() -> i32 {
        let src: i32[] = [7, 8, 9];
        let dst: i32[] = [0, 0, 0];
        memcpy(dst, src.data, 12);
        return dst.data[2];
    }
    """
    assert run(source).returncode == 9


def test_index_assignment_writes_through_pointers(run):
    """
    Elements are written through indexed pointers: plain, compound, and
    through an array's data pointer.
    """
    source = """
    @extern fn malloc(size: u64) -> opaque*;
    @extern fn free(ptr: opaque*);

    fn main() -> i32 {
        let values: i32* = malloc(12) as i32*;
        values[0] = 30;
        values[1] = 10;
        values[1] += 2;
        let total: i32 = values[0] + values[1];
        free(values);

        let arr: i32[] = [1, 2, 3];
        arr.data[2] = 58;
        return total + arr.data[2]; // 42 + 58
    }
    """
    assert run(source).returncode == 100


def test_member_assignment_through_an_indexed_pointer(run):
    """
    A struct field behind an indexed pointer is assignable: 'points[i].x = v'.
    """
    source = """
    @extern fn malloc(size: u64) -> opaque*;
    @extern fn free(ptr: opaque*);

    struct Point {
        x: i32;
        y: i32;
    }

    fn main() -> i32 {
        let points: Point* = malloc(16) as Point*;
        points[1].x = 25;
        points[1].x += 25;
        let x: i32 = points[1].x;
        free(points);
        return x;
    }
    """
    assert run(source).returncode == 50


def test_opaque_casts_back_to_a_typed_pointer(run):
    """
    An 'opaque*' becomes a typed pointer through an explicit cast: the
    malloc/free round trip.
    """
    source = """
    @extern fn calloc(count: u64, size: u64) -> opaque*;
    @extern fn free(ptr: opaque*);

    fn main() -> i32 {
        let values: i32* = calloc(3, 4) as i32*;
        let zero: i32 = values[0] + values[1] + values[2];
        free(values);
        return zero + 42;
    }
    """
    assert run(source).returncode == 42


def test_nested_array_literal_of_strings(run):
    """
    A 'char[][]' literal holds each string as its own fat array, each
    carrying its own length.
    """
    source = """
    fn main() -> i32 {
        let msgs: char[][] = ["hello", "world"];
        // 'w' is 119: the second message's first letter
        if (msgs.length == 2 and msgs.data[0].length == 5
                and msgs.data[1].data[0] == 119) {
            return 8;
        }
        return 0;
    }
    """
    assert run(source).returncode == 8
