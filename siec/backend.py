"""Native code emission and linking."""

import subprocess

from llvmlite import binding, ir


def compile_to_object(module: ir.Module, obj_path: str) -> None:
    """
    Verify an LLVM module and write native object code for the host target.
    """
    # register the host as the compilation target
    binding.initialize_native_target()
    binding.initialize_native_asmprinter()

    target_machine = binding.Target.from_default_triple().create_target_machine()
    module.triple = target_machine.triple

    # round-trip the IR through the LLVM binding and verify it
    llvm_module = binding.parse_assembly(str(module))
    llvm_module.verify()

    with open(obj_path, "wb") as f:
        f.write(target_machine.emit_object(llvm_module))


def link(obj_path: str, output: str) -> None:
    """
    Link an object file into an executable using the system C compiler.
    """
    subprocess.run(["cc", obj_path, "-o", output], check=True)
