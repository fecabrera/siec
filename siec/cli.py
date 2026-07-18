"""Command-line driver for the Sie compiler."""

import argparse
import os
import re
import sys
from pathlib import Path

from siec.backend import compile_to_object, emit_assembly, link, run_jit
from siec.codegen import codegen
from siec.loader import load_program


def display_path(path: str) -> str:
    """
    Show a source path relative to the current directory when that is shorter.
    """
    try:
        relative = os.path.relpath(path)
    except ValueError:
        return path

    return relative if len(relative) < len(path) else path


def format_error(source_name: str, error: Exception) -> str:
    """
    Render a compile error as '<source> at line <n>: <message>' when a line is known.

    The source names the file the error came from — an included file when the
    error carries one — falling back to the command-line source otherwise.
    """
    message = str(error)

    # errors from an included file carry their own source; others use the main one
    source = display_path(file) if (file := getattr(error, "sie_file", None)) else source_name

    # codegen errors carry the line as an attribute; lexer and parser errors
    # embed a 'line <n>:' prefix in their message instead
    line = getattr(error, "sie_line", None)
    if line is None:
        match = re.match(r"line (\d+): (.*)", message, re.DOTALL)
        if match:
            line, message = match.group(1), match.group(2)

    if line is not None:
        return f"{source} at line {line}: {message}"

    return f"{source}: {message}"


def main() -> int:
    """
    Run the compiler: parse arguments, compile the source file, and link it.
    """
    args = argparse.ArgumentParser(prog="siec", description="Sie language compiler")
    args.add_argument("sources", nargs="+")
    args.add_argument("-o", "--output", default=None)
    args.add_argument("-c", action="store_true", dest="compile_only",
                      help="compile to an object file, without linking")
    args.add_argument("-I", "--include", action="append", default=[],
                      help="add a directory to the include search path")
    args.add_argument("-l", action="append", default=[], dest="libs", metavar="LIB",
                      help="link against a library (passed to the linker as -l<lib>)")
    args.add_argument("-L", action="append", default=[], dest="lib_dirs", metavar="DIR",
                      help="add a directory to the library search path")
    args.add_argument("--emit-llvm", action="store_true", help="print LLVM IR and exit")
    args.add_argument("--emit-asm", action="store_true",
                      help="print native assembly and exit")
    args.add_argument("--run", nargs=argparse.REMAINDER,
                      help="jit-run the program instead of building, "
                           "passing along any following arguments")
    opts = args.parse_args()

    # '.o' files on the command line skip the front end and join the link
    objects = [s for s in opts.sources if s.endswith(".o")]
    sources = [Path(s) for s in opts.sources if not s.endswith(".o")]
    if not sources:
        print("siec: no source files", file=sys.stderr)
        return 1

    # 'lib/' next to each source file is always on the include path
    include_paths = [Path(p) for p in opts.include]
    for source in sources:
        lib = source.resolve().parent / "lib"

        if lib not in include_paths:
            include_paths.append(lib)

    # front end: sources and includes -> AST -> LLVM module, reporting the
    # first compile error in human-readable form instead of a traceback
    try:
        program = load_program(sources, include_paths)
        module = codegen(program, str(sources[0]))
    except (SyntaxError, TypeError, NameError, FileNotFoundError) as error:
        print(format_error(str(sources[0]), error), file=sys.stderr)
        return 1

    if opts.emit_llvm:
        print(module)
        return 0

    if opts.emit_asm:
        print(emit_assembly(module), end="")
        return 0

    # '-c' stops after native code generation, leaving only the object file,
    # named after the first source, cc-style, unless '-o' says otherwise
    if opts.compile_only:
        compile_to_object(module, opts.output or str(Path(sources[0].name).with_suffix(".o")))
        return 0

    # jit-run in place of building, exiting with the program's own code;
    # the program's argv is the source path plus the arguments after --run
    if opts.run is not None:
        try:
            return run_jit(module, [str(sources[0]), *opts.run], objects)
        except NameError as error:
            print(format_error(str(sources[0]), error), file=sys.stderr)
            return 1

    # back end: LLVM module -> object file -> executable, joined by the
    # object files given on the command line
    output = opts.output or "a.out"
    obj_path = output + ".o"
    compile_to_object(module, obj_path)
    link([obj_path, *objects], output, opts.libs, opts.lib_dirs)
    return 0
