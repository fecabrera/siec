"""Native code emission, linking, and JIT execution."""

import ctypes
import subprocess

from llvmlite import binding, ir


def prepare_module(module: ir.Module) -> tuple:
    """
    Verify an LLVM module against the host target, returning the target
    machine and the module round-tripped through the LLVM binding.
    """
    # register the host as the compilation target
    binding.initialize_native_target()
    binding.initialize_native_asmprinter()

    target_machine = binding.Target.from_default_triple().create_target_machine()
    module.triple = target_machine.triple

    # round-trip the IR through the LLVM binding and verify it
    llvm_module = binding.parse_assembly(str(module))
    llvm_module.verify()

    return target_machine, llvm_module


def compile_to_object(module: ir.Module, obj_path: str) -> None:
    """
    Verify an LLVM module and write native object code for the host target.
    """
    target_machine, llvm_module = prepare_module(module)

    with open(obj_path, "wb") as f:
        f.write(target_machine.emit_object(llvm_module))


def emit_assembly(module: ir.Module) -> str:
    """
    Verify an LLVM module and render native assembly for the host target.
    """
    target_machine, llvm_module = prepare_module(module)
    return target_machine.emit_assembly(llvm_module)


def run_jit(module: ir.Module, argv: list[str], objects: list[str] = ()) -> int:
    """
    JIT-compile a module in-process and run its main, returning its exit code.

    Extra object files are loaded into the engine, their symbols resolvable
    from the program like any linked code.
    """
    target_machine, llvm_module = prepare_module(module)

    with binding.create_mcjit_compiler(llvm_module, target_machine) as engine:
        for path in objects:
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
    subprocess.run(["cc", *obj_paths, "-o", output, *flags], check=True)
