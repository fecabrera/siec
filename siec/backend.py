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


def run_jit(module: ir.Module, argv: list[str]) -> int:
    """
    JIT-compile a module in-process and run its main, returning its exit code.
    """
    target_machine, llvm_module = prepare_module(module)

    with binding.create_mcjit_compiler(llvm_module, target_machine) as engine:
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


def link(obj_path: str, output: str) -> None:
    """
    Link an object file into an executable using the system C compiler.
    """
    subprocess.run(["cc", obj_path, "-o", output], check=True)
