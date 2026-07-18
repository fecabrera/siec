"""Feature tests for '@packed' and '@align(N)' struct layouts."""


def test_packed_struct_drops_padding(compile_source):
    """
    '@packed' lowers to LLVM's packed struct form: no padding between fields.
    """
    module = str(compile_source("""
    @packed struct Header {
        tag: u8;
        size: u32;
    }

    fn main() -> i32 {
        let h: Header = {1, 2};
        return h.tag as i32;
    }
    """))
    assert 'type <{i8, i32}>' in module


def test_packed_struct_fields_read_and_write(run):
    """
    Field access goes by the packed offsets, invisible to the program.
    """
    source = """
    @packed struct Header {
        tag: u8;
        size: u32;
        flag: u8;
    }

    fn main() -> i32 {
        let h: Header = { 1, 40, 0 };
        h.flag = 1;
        return h.tag as i32 + h.size as i32 + h.flag as i32; // 42
    }
    """
    assert run(source).returncode == 42


def test_aligned_struct_allocations(compile_source):
    """
    '@align(N)' puts N on every allocation of the struct: locals,
    parameters, and static globals.
    """
    module = str(compile_source("""
    @align(64) struct CacheLine {
        hot: i64;
    }

    @static let shared: CacheLine;

    fn read(c: CacheLine) -> i64 {
        return c.hot;
    }

    fn main() -> i32 {
        let local: CacheLine = { 1 };
        return (read(local) + shared.hot) as i32;
    }
    """))
    assert module.count("align 64") >= 3  # the global, the local, the parameter


def test_packed_and_aligned_combine(compile_source):
    """
    '@packed @align(N)' applies both layouts to one struct.
    """
    module = str(compile_source("""
    @packed @align(16) struct S {
        a: u8;
        b: i64;
    }

    fn main() -> i32 {
        let s: S = { 1, 2 };
        return s.a as i32;
    }
    """))
    assert 'type <{i8, i64}>' in module
    assert "align 16" in module
