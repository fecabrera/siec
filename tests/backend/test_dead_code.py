"""Whole-program removal of unreachable function bodies."""


def definitions(module) -> set[str]:
    """Names of functions with bodies in an LLVM module."""
    return {fn.name for fn in module.functions if fn.blocks}


def test_application_emits_only_reachable_ordinary_functions(compile_source):
    source = """
    fn used() -> i32 { return 7; }
    fn unused() -> i32 { return 9; }

    fn main() -> i32 { return used() - 7; }
    """

    names = definitions(compile_source(source))
    assert "main" in names
    assert "used()" in names
    assert "unused()" not in names


def test_unreachable_generic_instances_have_no_body(compile_source):
    source = """
    fn identity<T>(value: T) -> T { return value; }
    fn unused() -> i32 { return identity(7); }

    fn main() -> i32 { return 0; }
    """

    names = definitions(compile_source(source))
    assert "unused()" not in names
    assert not any(name.startswith("identity<") for name in names)


def test_runtime_interface_arms_follow_reachable_any_types(
        compile_source, run):
    source = """
    interface Value {
        fn value(const &self) -> i32;
    }

    struct Used: Value { n: i32; }
    fn Used::value(const &self) -> i32 { return self.n; }

    struct Unused: Value { n: i32; }
    fn Unused::value(const &self) -> i32 { return self.n; }

    fn read(args...) -> i32 {
        case (@typeof(args[0])) {
        when Value:
            let value = args[0] as Value;
            return value.value();
        }
        return 0;
    }

    fn unreachable() -> i32 {
        let value: Unused = { 9 };
        return read(value);
    }

    fn main() -> i32 {
        let value: Used = { 7 };
        return read(value) - 7;
    }
    """

    names = definitions(compile_source(source))
    assert "Used::value(&Used)" in names
    assert "Unused::value(&Unused)" not in names
    assert run(source).returncode == 0


def test_runtime_interface_arm_needs_a_reachable_any_producer(compile_source):
    source = """
    interface Value {
        fn value(const &self) -> i32;
    }

    struct Unused: Value { n: i32; }
    fn Unused::value(const &self) -> i32 { return self.n; }

    fn read(value: Any) -> i32 {
        case (@typeof(value)) {
        when Value:
            let concrete = value as Value;
            return concrete.value();
        }
        return 0;
    }

    fn main() -> i32 { return 0; }
    """

    names = definitions(compile_source(source))
    assert "Unused::value(&Unused)" not in names


def test_unit_without_main_keeps_public_function_bodies(compile_source):
    source = """
    fn first() -> i32 { return 1; }
    fn second() -> i32 { return 2; }
    """

    names = definitions(compile_source(source))
    assert {"first()", "second()"} <= names
