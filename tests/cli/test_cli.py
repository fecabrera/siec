"""Tests for siec.cli."""

import subprocess
import sys

from siec.cli import format_error, main


def run_cli(monkeypatch, *argv):
    """
    Invoke the compiler's main() with the given command-line arguments.
    """
    monkeypatch.setattr(sys, "argv", ["siec", *map(str, argv)])
    return main()


def test_emit_llvm_prints_the_module(tmp_path, capsys, monkeypatch):
    """
    --emit-llvm prints the module's IR instead of building.
    """
    source = """\
    fn main() -> i32 { return 0; }
    """

    src = tmp_path / "p.sie"
    src.write_text(source)
    
    assert run_cli(monkeypatch, src, "--emit-llvm") == 0
    
    out = capsys.readouterr().out
    assert 'define i32 @"main"' in out


def test_emit_asm_prints_native_assembly(tmp_path, capsys, monkeypatch):
    """
    --emit-asm prints the host target's assembly instead of building.
    """
    source = """\
    fn main() -> i32 { return 0; }
    """

    src = tmp_path / "p.sie"
    src.write_text(source)

    assert run_cli(monkeypatch, src, "--emit-asm") == 0

    out = capsys.readouterr().out
    assert "main" in out          # the entry symbol's label
    assert "define" not in out    # assembly, not LLVM IR


def test_static_functions_are_local_to_their_file(tmp_path, monkeypatch):
    """
    Two files may each declare a '@static' of the same name; each file
    resolves its own.
    """
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "util.sie").write_text("""\
    @static fn helper() -> i32 { return 20; }
    fn util_value() -> i32 { return helper() + 1; }
    """)

    src = tmp_path / "p.sie"
    src.write_text("""\
    @include("util")
    @static fn helper() -> i32 { return 21; }
    fn main() -> i32 { return helper() + util_value(); }
    """)

    exe = tmp_path / "p"
    assert run_cli(monkeypatch, src, "-o", exe) == 0
    assert subprocess.run([str(exe)]).returncode == 42


def test_another_files_static_is_undefined(tmp_path, monkeypatch, capsys):
    """
    A static never resolves from outside its own file.
    """
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "util.sie").write_text(
        "@static fn helper() -> i32 { return 1; }")

    src = tmp_path / "p.sie"
    src.write_text("""\
    @include("util")
    fn main() -> i32 { return helper(); }
    """)

    assert run_cli(monkeypatch, src, "-o", tmp_path / "p") == 1
    assert "undefined function 'helper'" in capsys.readouterr().err


def test_static_globals_are_local_to_their_file(tmp_path, monkeypatch):
    """
    Two files may each hold a '@static let' of the same name, and another
    file's static never resolves.
    """
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "util.sie").write_text("""\
    @static let count: i32 = 100;
    fn util_count() -> i32 { count += 1; return count; }
    """)

    src = tmp_path / "p.sie"
    src.write_text("""\
    @include("util")
    @static let count: i32 = 40;
    fn main() -> i32 { count += 1; return count + util_count() - 101; } // 41+101-101
    """)

    exe = tmp_path / "p"
    assert run_cli(monkeypatch, src, "-o", exe) == 0
    assert subprocess.run([str(exe)]).returncode == 41


def test_another_files_static_global_is_undefined(tmp_path, monkeypatch, capsys):
    """
    Reading a static global from outside its file is an undefined variable.
    """
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "util.sie").write_text("@static let count: i32 = 1;")

    src = tmp_path / "p.sie"
    src.write_text("""\
    @include("util")
    fn main() -> i32 { return count; }
    """)

    assert run_cli(monkeypatch, src, "-o", tmp_path / "p") == 1
    assert "undefined variable 'count'" in capsys.readouterr().err


def test_optimization_level_runs_the_pipeline(tmp_path, capsys, monkeypatch):
    """
    '-O2' runs LLVM's pass pipeline: the emitted IR is folded to a constant.
    """
    source = """\
    fn main() -> i32 {
        let x: i32 = 6;
        return x * 7;
    }
    """

    src = tmp_path / "p.sie"
    src.write_text(source)

    assert run_cli(monkeypatch, src, "-O2", "--emit-llvm") == 0
    assert "ret i32 42" in capsys.readouterr().out


def test_default_is_unoptimized(tmp_path, capsys, monkeypatch):
    """
    Without '-O', --emit-llvm prints the IR as generated, slots and all.
    """
    source = """\
    fn main() -> i32 {
        let x: i32 = 6;
        return x * 7;
    }
    """

    src = tmp_path / "p.sie"
    src.write_text(source)

    assert run_cli(monkeypatch, src, "--emit-llvm") == 0
    assert "alloca" in capsys.readouterr().out


def test_optimized_executable_still_runs(tmp_path, monkeypatch):
    """
    '-O3' carries through object emission and linking.
    """
    source = """\
    fn main() -> i32 {
        let total: i32 = 0;
        for (let i = 1; i <= 9; i += 1) {
            total += i;
        }
        return total; // 45
    }
    """

    src = tmp_path / "p.sie"
    src.write_text(source)
    exe = tmp_path / "p"

    assert run_cli(monkeypatch, src, "-O3", "-o", exe) == 0
    assert subprocess.run([str(exe)]).returncode == 45


def test_compiles_and_links_an_executable(tmp_path, monkeypatch):
    """
    The default pipeline produces a runnable executable at -o.
    """
    source = """\
    fn main() -> i32 { return 5; }
    """

    src = tmp_path / "p.sie"
    src.write_text(source)
    exe = tmp_path / "p"
    
    assert run_cli(monkeypatch, src, "-o", exe) == 0
    assert subprocess.run([str(exe)]).returncode == 5


def test_compiles_multiple_sources_together(tmp_path, monkeypatch):
    """
    Several source files on the command line build into one executable.
    """
    main_source = """\
    fn helper() -> i32; fn main() -> i32 { return helper(); }
    """
    impl_source = """\
    fn helper() -> i32 { return 3; }
    """
    
    main_src = tmp_path / "main.sie"
    main_src.write_text(main_source)
    
    impl = tmp_path / "impl.sie"
    impl.write_text(impl_source)
    
    exe = tmp_path / "p"
    
    assert run_cli(monkeypatch, main_src, impl, "-o", exe) == 0
    assert subprocess.run([str(exe)]).returncode == 3


def test_struct_may_be_declared_in_a_later_source(tmp_path, monkeypatch):
    """
    A source may use a struct that only a later source file declares.
    """
    use_source = """\
    fn main() -> i32 {
        let p: Pair;
        p.a = 10;
        p.b = 20;
        return p.a + p.b;
    }
    """
    decl_source = """\
    struct Pair {
        a: i32;
        b: i32;
    }
    """

    use_src = tmp_path / "use.sie"
    use_src.write_text(use_source)

    decl_src = tmp_path / "decl.sie"
    decl_src.write_text(decl_source)

    assert run_cli(monkeypatch, use_src, decl_src, "--run") == 30


def test_compile_only_emits_an_object_file(tmp_path, monkeypatch):
    """
    -c compiles a source to an object file, named after it, without linking;
    the object then links into a later build.
    """
    helper_source = """\
    fn triple(x: i32) -> i32 { return x * 3; }
    """
    main_source = """\
    fn triple(x: i32) -> i32;
    fn main() -> i32 { return triple(14); }
    """

    helper = tmp_path / "helper.sie"
    helper.write_text(helper_source)

    # the default object name is the source's, cc-style, in the working directory
    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, helper, "-c") == 0
    assert (tmp_path / "helper.o").exists()

    src = tmp_path / "p.sie"
    src.write_text(main_source)

    exe = tmp_path / "p"
    assert run_cli(monkeypatch, src, tmp_path / "helper.o", "-o", exe) == 0
    assert subprocess.run([str(exe)]).returncode == 42


def test_compile_only_honors_the_output_path(tmp_path, monkeypatch):
    """
    -c with -o writes the object exactly where -o points.
    """
    source = """\
    fn f() -> i32 { return 1; }
    """

    src = tmp_path / "p.sie"
    src.write_text(source)

    obj = tmp_path / "custom.o"
    assert run_cli(monkeypatch, src, "-c", "-o", obj) == 0
    assert obj.exists()


def test_constants_resolve_across_included_files(tmp_path, monkeypatch):
    """
    An '@const' declared in an included file substitutes in the includer.
    """
    dep_source = """\
    @const ANSWER: i32 = 42;
    """

    main_source = """\
    @include("dep")
    fn main() -> i32 { return ANSWER; }
    """

    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "dep.sie").write_text(dep_source)

    src = tmp_path / "p.sie"
    src.write_text(main_source)
    assert run_cli(monkeypatch, src, "--run") == 42


def test_object_files_join_the_link(tmp_path, monkeypatch):
    """
    '.o' files on the command line skip the front end and link into the build.
    """
    obj_src = tmp_path / "magic.c"
    obj_src.write_text("int magic(void) { return 21; }\n")
    obj = tmp_path / "magic.o"
    subprocess.run(["cc", "-c", str(obj_src), "-o", str(obj)], check=True)

    source = """\
    @extern fn magic() -> i32;
    fn main() -> i32 { return magic() * 2; }
    """

    src = tmp_path / "p.sie"
    src.write_text(source)

    exe = tmp_path / "p"
    assert run_cli(monkeypatch, src, obj, "-o", exe) == 0
    assert subprocess.run([str(exe)]).returncode == 42


def test_object_files_resolve_under_run(tmp_path, monkeypatch):
    """
    --run loads given '.o' files into the JIT, resolving their symbols.
    """
    obj_src = tmp_path / "magic.c"
    obj_src.write_text("int magic(void) { return 21; }\n")
    obj = tmp_path / "magic.o"
    subprocess.run(["cc", "-c", str(obj_src), "-o", str(obj)], check=True)

    source = """\
    @extern fn magic() -> i32;
    fn main() -> i32 { return magic() * 2; }
    """

    src = tmp_path / "p.sie"
    src.write_text(source)
    assert run_cli(monkeypatch, src, obj, "--run") == 42


def test_only_object_files_is_an_error(tmp_path, monkeypatch, capsys):
    """
    A command line with no Sie sources exits non-zero with a readable error.
    """
    obj = tmp_path / "x.o"
    obj.write_bytes(b"")
    assert run_cli(monkeypatch, obj) == 1
    assert "no source files" in capsys.readouterr().err


def test_links_against_libraries(tmp_path, monkeypatch):
    """
    -L adds a library search path and -l links the named library into the build.
    """
    import os
    import sys as _sys

    # a one-function C library for the program to call
    c_source = """\
    int magic(void) { return 33; }
    """

    lib_src = tmp_path / "magic.c"
    lib_src.write_text(c_source)

    suffix = "dylib" if _sys.platform == "darwin" else "so"
    subprocess.run(["cc", "-shared", str(lib_src), "-o", str(tmp_path / f"libmagic.{suffix}")], check=True)

    source = """\
    @extern fn magic() -> i32;
    fn main() -> i32 { return magic(); }
    """

    src = tmp_path / "p.sie"
    src.write_text(source)

    exe = tmp_path / "p"
    assert run_cli(monkeypatch, src, "-L", tmp_path, "-l", "magic", "-o", exe) == 0

    env = {**os.environ, "LD_LIBRARY_PATH": str(tmp_path), "DYLD_LIBRARY_PATH": str(tmp_path)}
    assert subprocess.run([str(exe)], env=env).returncode == 33


def test_run_jits_the_program(tmp_path, monkeypatch):
    """
    --run executes the program in-process and returns its exit code.
    """
    source = """\
    fn main() -> i32 { return 7; }
    """
    
    src = tmp_path / "p.sie"
    src.write_text(source)
    assert run_cli(monkeypatch, src, "--run") == 7


def test_run_passes_arguments_after_the_flag(tmp_path, monkeypatch):
    """
    Arguments after --run reach the program as its argv, after the source path.
    """
    source = """\
    fn main(argc: i32, argv: char**) -> i32 { return argc; }
    """
    
    src = tmp_path / "p.sie"
    src.write_text(source)
    assert run_cli(monkeypatch, src, "--run", "a", "b") == 3


def test_run_reaches_libc(tmp_path, monkeypatch, capfd):
    """
    A jit-run program resolves libc symbols; printf's output reaches stdout.
    """
    source = """\
    @extern fn printf(fmt: char*, ...) -> i32;
    fn main() -> i32 { printf("jit says %d\\n", 42); return 0; }
    """
    
    src = tmp_path / "p.sie"
    src.write_text(source)
    assert run_cli(monkeypatch, src, "--run") == 0
    assert "jit says 42" in capfd.readouterr().out


def test_run_loads_missing_libraries_with_a_clean_error(tmp_path, monkeypatch, capsys):
    """
    '-l' reaches the JIT: an unloadable library reports cleanly instead of
    leaving its symbols null to segfault on.
    """
    src = tmp_path / "p.sie"
    src.write_text("fn main() -> i32 { return 0; }")

    assert run_cli(monkeypatch, src, "-l", "nope_no_such", "--run") == 1

    err = capsys.readouterr().err
    assert "cannot load library 'nope_no_such'" in err
    assert "Traceback" not in err


def test_run_without_a_main_is_an_error(tmp_path, monkeypatch, capsys):
    """
    --run on a program with no main exits non-zero with a readable error.
    """
    source = """\
    fn helper() -> i32 { return 1; }
    """
    
    src = tmp_path / "p.sie"
    src.write_text(source)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "no 'main' function" in capsys.readouterr().err


def test_lib_next_to_the_source_is_on_the_include_path(tmp_path, monkeypatch):
    """
    Includes resolve through the lib/ directory beside the source file by default.
    """
    dep_source = """\
    fn dep() -> i32 { return 2; }
    """

    main_source = """\
    @include("dep") fn main() -> i32 { return dep(); }
    """

    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "dep.sie").write_text(dep_source)

    src = tmp_path / "p.sie"
    src.write_text(main_source)
    exe = tmp_path / "p"
    assert run_cli(monkeypatch, src, "-o", exe) == 0
    assert subprocess.run([str(exe)]).returncode == 2


def test_include_flag_adds_search_paths(tmp_path, monkeypatch, capsys):
    """
    -I directories are searched when resolving includes.
    """
    dep_source = """\
    fn dep() -> i32 { return 1; }
    """

    main_source = """
    @include("dep") fn main() -> i32 { return dep(); }
    """

    inc = tmp_path / "vendor"
    inc.mkdir()
    (inc / "dep.sie").write_text(dep_source)

    src = tmp_path / "p.sie"
    src.write_text(main_source)
    assert run_cli(monkeypatch, src, "-I", inc, "--emit-llvm") == 0
    assert "dep" in capsys.readouterr().out


def test_includes_resolve_from_the_working_directory(tmp_path, monkeypatch, capsys):
    """
    An include missing beside the source resolves relative to the cwd.
    """
    dep_source = """\
    fn dep() -> i32 { return 1; }
    """

    main_source = """\
    @include("vendor/dep") fn main() -> i32 { return dep(); }
    """

    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "dep.sie").write_text(dep_source)

    src = tmp_path / "src" / "p.sie"
    src.parent.mkdir()
    src.write_text(main_source)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--emit-llvm") == 0
    assert "dep" in capsys.readouterr().out


def test_includes_resolve_from_lib_under_the_working_directory(tmp_path, monkeypatch, capsys):
    """
    The cwd's lib/ is searched too, so a project compiles from its root.
    """
    dep_source = """\
    fn dep() -> i32 { return 1; }
    """

    main_source = """\
    @include("libc/dep") fn main() -> i32 { return dep(); }
    """

    (tmp_path / "lib" / "libc").mkdir(parents=True)
    (tmp_path / "lib" / "libc" / "dep.sie").write_text(dep_source)

    src = tmp_path / "examples" / "p.sie"
    src.parent.mkdir()
    src.write_text(main_source)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--emit-llvm") == 0
    assert "dep" in capsys.readouterr().out


def test_format_error_uses_the_line_attribute():
    """
    A codegen error carrying 'sie_line' renders as '<source> at line <n>: <message>'.
    """
    error = TypeError("undefined variable 'x'")
    error.sie_line = 7
    assert format_error("p.sie", error) == "p.sie at line 7: undefined variable 'x'"


def test_format_error_parses_the_line_prefix():
    """
    A lexer or parser error renders its embedded 'line <n>:' as the location.
    """
    error = SyntaxError("line 3: expected ';', got '}'")
    assert format_error("p.sie", error) == "p.sie at line 3: expected ';', got '}'"


def test_format_error_without_a_line():
    """
    An error with no line information renders as '<source>: <message>'.
    """
    error = FileNotFoundError("cannot resolve include 'x'")
    assert format_error("p.sie", error) == "p.sie: cannot resolve include 'x'"


def test_format_error_prefers_the_errors_own_file():
    """
    An error tagged with its own file names that file, not the command-line source.
    """
    error = NameError("undefined variable 'x'")
    error.sie_line = 4
    error.sie_file = "lib/dep.sie"
    assert format_error("app.sie", error) == "lib/dep.sie at line 4: undefined variable 'x'"


def test_codegen_error_is_reported_with_a_line(tmp_path, monkeypatch, capsys):
    """
    A codegen error exits non-zero and prints the source and line, not a traceback.
    """
    source = """\
    fn main() -> i32 {
        return missing;
    }
    """
    
    src = tmp_path / "p.sie"
    src.write_text(source)

    assert run_cli(monkeypatch, src, "-o", tmp_path / "p") == 1

    err = capsys.readouterr().err
    assert "at line 2: undefined variable 'missing'" in err
    assert "Traceback" not in err


def test_widening_error_reports_the_declaration_line(tmp_path, monkeypatch, capsys):
    """
    The implicit-conversion error points at the offending statement's line.
    """
    source = """\
    fn main() -> i32 {
        let a: i32 = 0;
        let b: u32 = a;
        return 0;
    }
    """
    
    src = tmp_path / "p.sie"
    src.write_text(source)
    assert run_cli(monkeypatch, src, "-o", tmp_path / "p") == 1

    err = capsys.readouterr().err
    assert "at line 3:" in err
    assert "Traceback" not in err


def test_parse_error_is_reported_without_a_traceback(tmp_path, monkeypatch, capsys):
    """
    A parse error exits non-zero with a readable message and no traceback.
    """
    # the missing ';' after 'return 1' is the parse error under test
    source = """\
    fn main() -> i32 {
        return 1
    }
    """
    
    src = tmp_path / "p.sie"
    src.write_text(source)
    assert run_cli(monkeypatch, src, "-o", tmp_path / "p") == 1

    err = capsys.readouterr().err
    assert "at line" in err
    assert "Traceback" not in err


def test_error_in_an_included_file_names_that_file(tmp_path, monkeypatch, capsys):
    """
    A compile error inside an included file reports that file, not the includer.
    """
    dep_source = """\
    fn dep() -> i32 {
        return missing;
    }
    """

    main_source = """\
    @include("dep")
    
    fn main() -> i32 { return dep(); }
    """

    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "dep.sie").write_text(dep_source)
    
    src = tmp_path / "app.sie"
    src.write_text(main_source)
    
    assert run_cli(monkeypatch, src, "-o", tmp_path / "p") == 1

    err = capsys.readouterr().err
    assert "dep.sie at line 2: undefined variable 'missing'" in err
    assert "app.sie" not in err
    assert "Traceback" not in err


def test_target_aims_the_module_at_the_triple(tmp_path, capsys, monkeypatch):
    """
    --target sets the module's triple and the target constants; -O 1
    folds them into the IR where the test can read them.
    """
    src = tmp_path / "p.sie"
    src.write_text("""
    fn main() -> i32 {
        return TARGET_OS * 10 + TARGET_ARCH + (sizeof(opaque*) as i32) * 100;
    }
    """)

    assert run_cli(monkeypatch, src, "--target", "x86_64-unknown-linux-gnu",
                   "-O", "1", "--emit-llvm") == 0

    out = capsys.readouterr().out
    assert 'target triple = "x86_64-unknown-linux-gnu"' in out
    assert "ret i32 821" in out  # OS_LINUX*10 + ARCH_X86_64 + 8 * 100


def test_target_layout_decides_sizeof(tmp_path, capsys, monkeypatch):
    """
    A 32-bit target's pointers measure 4 bytes at compile time.
    """
    src = tmp_path / "p.sie"
    src.write_text("fn main() -> i32 { return sizeof(opaque*) as i32; }")

    assert run_cli(monkeypatch, src, "--target", "i686-unknown-linux-gnu",
                   "-O", "1", "--emit-llvm") == 0
    assert "ret i32 4" in capsys.readouterr().out


def test_target_writes_a_cross_object(tmp_path, monkeypatch):
    """
    -c with --target writes an object file for the foreign target.
    """
    src = tmp_path / "p.sie"
    src.write_text("fn main() -> i32 { return 0; }")
    obj = tmp_path / "cross.o"

    assert run_cli(monkeypatch, src, "--target", "x86_64-unknown-linux-gnu",
                   "-c", "-o", obj) == 0

    # an ELF object starts with the \x7fELF magic, unlike host Mach-O
    assert obj.read_bytes()[:4] == b"\x7fELF"


def test_run_refuses_a_foreign_target(tmp_path, capsys, monkeypatch):
    """
    The JIT runs in-process, so --run only accepts the host's triple.
    """
    src = tmp_path / "p.sie"
    src.write_text("fn main() -> i32 { return 0; }")

    # riscv64 is nobody's CI host, so the triple never matches
    assert run_cli(monkeypatch, src, "--target", "riscv64-unknown-linux-gnu",
                   "--run") == 1
    assert "cannot jit-run" in capsys.readouterr().err
