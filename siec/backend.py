"""Native code emission, linking, and JIT execution."""

import ctypes
import subprocess
import sys
from pathlib import Path

from llvmlite import binding, ir


def prepare_module(module: ir.Module, opt: int = 0, target: str | None = None) -> tuple:
    """
    Verify an LLVM module against its target — the host, or the triple
    given — returning the target machine and the module round-tripped
    through the LLVM binding.

    An optimization level above 0 runs LLVM's standard pass pipeline over
    the module, cc-style: -O1 through -O3.
    """
    # register the host as the compilation target; a cross target needs
    # every backend registered, not just the host's; the asm parser
    # reads '@asm' bodies back into machine code
    binding.initialize_native_target()
    binding.initialize_native_asmprinter()
    binding.initialize_native_asmparser()

    if target is not None:
        binding.initialize_all_targets()
        binding.initialize_all_asmprinters()
        machine = binding.Target.from_triple(target)
    else:
        machine = binding.Target.from_default_triple()

    target_machine = machine.create_target_machine(opt=opt)
    module.triple = target_machine.triple

    # round-trip the IR through the LLVM binding and verify it
    llvm_module = binding.parse_assembly(str(module))
    llvm_module.verify()

    options = binding.create_pipeline_tuning_options(speed_level=opt)
    pass_builder = binding.create_pass_builder(target_machine, options)

    if opt > 0:
        pass_builder.getModulePassManager().run(llvm_module, pass_builder)
    else:
        # '@inline' functions inline even unoptimized: the standard pipeline
        # honors 'alwaysinline' on its own, but -O0 runs no pipeline, so the
        # always-inliner runs alone
        manager = binding.ModulePassManager()
        manager.add_always_inliner_pass()
        manager.run(llvm_module, pass_builder)

    return target_machine, llvm_module


def compile_to_object(module: ir.Module, obj_path: str, opt: int = 0,
                      target: str | None = None) -> None:
    """
    Verify an LLVM module and write object code for its target.
    """
    target_machine, llvm_module = prepare_module(module, opt, target)

    with open(obj_path, "wb") as f:
        f.write(target_machine.emit_object(llvm_module))


def emit_assembly(module: ir.Module, opt: int = 0, target: str | None = None) -> str:
    """
    Verify an LLVM module and render assembly for its target.
    """
    target_machine, llvm_module = prepare_module(module, opt, target)
    return target_machine.emit_assembly(llvm_module)


def emit_llvm(module: ir.Module, opt: int = 0, target: str | None = None) -> str:
    """
    Render a module's LLVM IR: as generated at -O0, after the optimization
    pipeline otherwise.
    """
    if opt == 0:
        return str(module)

    return str(prepare_module(module, opt, target)[1])


def load_library(name: str, lib_dirs: list[str]) -> None:
    """
    Load a '-l' library into the process so the JIT can resolve its symbols,
    searching the '-L' directories first and the system's paths after.
    """
    extension = "dylib" if sys.platform == "darwin" else "so"
    filename = f"lib{name}.{extension}"

    # a candidate from a '-L' directory must exist; the bare filename is
    # left for the dynamic loader to search its default paths
    candidates = [str(path) for d in lib_dirs if (path := Path(d) / filename).is_file()]

    for candidate in [*candidates, filename]:
        try:
            binding.load_library_permanently(candidate)
            return
        except RuntimeError:
            continue

    raise NameError(f"cannot load library {name!r}")


def add_archive(engine, path: str) -> None:
    """
    Load a static library's members into the JIT engine: the archive is
    unpacked in place, each object joining like one given directly.

    Both archive flavors are read: GNU's, with its '//' long-name table,
    and BSD's, with '#1/<n>' names prefixed to the member's data.
    """
    data = Path(path).read_bytes()
    if data[:8] != b"!<arch>\n":
        raise NameError(f"{path!r} is not a static library")

    position = 8
    while position + 60 <= len(data):
        header = data[position:position + 60]
        position += 60

        name = header[:16].decode("ascii", "replace").strip()
        size = int(header[48:58])
        content = data[position:position + size]
        position += size + (size & 1)  # members align to two bytes

        # a BSD long name rides at the front of the member's data
        if name.startswith("#1/"):
            length = int(name[3:])
            name = content[:length].decode("ascii", "replace").rstrip("\0")
            content = content[length:]

        # symbol tables and the name table describe members, they aren't
        # ones; a '/<n>' name is a real member, named through the table
        if name in ("/", "//", "/SYM64/") or name.startswith("__.SYMDEF"):
            continue

        engine.add_object_file(binding.ObjectFileRef.from_data(content))


def run_jit(module: ir.Module, argv: list[str], objects: list[str] = (),
            libs: list[str] = (), lib_dirs: list[str] = (), opt: int = 0) -> int:
    """
    JIT-compile a module in-process and run its main, returning its exit code.

    Extra object files are loaded into the engine, and '-l' libraries into
    the process, their symbols resolvable from the program like any linked code.
    """
    target_machine, llvm_module = prepare_module(module, opt)

    for name in libs:
        load_library(name, lib_dirs)

    with binding.create_mcjit_compiler(llvm_module, target_machine) as engine:
        for path in objects:
            if str(path).endswith(".a"):
                add_archive(engine, path)
            else:
                engine.add_object_file(binding.ObjectFileRef.from_path(path))

        engine.finalize_object()
        engine.run_static_constructors()

        address = engine.get_function_address("main")
        if not address:
            raise NameError("program has no 'main' function")

        # call main(argc, argv) through the C ABI; a main declared with
        # fewer parameters simply ignores the extra arguments
        c_argv = (ctypes.c_char_p * (len(argv) + 1))(*[a.encode() for a in argv], None)
        c_main = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_int32,
                                  ctypes.POINTER(ctypes.c_char_p))(address)

        code = c_main(len(argv), c_argv)
        engine.run_static_destructors()

        # returning from main skips the C runtime's exit-time flush, which
        # would strand buffered stdio output in this still-running process
        ctypes.CDLL(None).fflush(None)
        return code


def link(obj_paths: list[str], output: str, libs: list[str] = (),
         lib_dirs: list[str] = ()) -> None:
    """
    Link object files into an executable using the system C compiler,
    against the named libraries, searched in the given directories.
    """
    flags = [f"-L{d}" for d in lib_dirs] + [f"-l{name}" for name in libs]
    try:
        process = subprocess.run(["cc", *obj_paths, "-o", output, *flags])
    except FileNotFoundError:
        raise OSError("no 'cc' on this system to link with") from None

    # the linker has already reported its own errors on stderr
    if process.returncode != 0:
        raise OSError("linking failed")
