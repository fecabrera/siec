"""Native code emission, linking, and JIT execution."""

import ctypes
import ctypes.util
import multiprocessing
import signal
import subprocess
import sys
from pathlib import Path

from llvmlite import binding, ir

from siec.diagnostics import InputFormatError


class TargetError(Exception):
    """An LLVM target triple that the compiler cannot use."""


class ObjectFormatError(InputFormatError):
    """An object file or archive that is unsafe to hand to LLVM."""


def _target_machine(target: str | None = None, opt: int = 0,
                    jit: bool = False):
    """
    Create a machine for the requested target, translating LLVM's target
    lookup failures into a compiler-facing error.
    """
    # register the host as the compilation target; a cross target needs
    # every backend registered, not just the host's; the asm parser
    # reads '@asm' bodies back into machine code
    binding.initialize_native_target()
    binding.initialize_native_asmprinter()
    binding.initialize_native_asmparser()

    requested = target or binding.get_default_triple()

    try:
        if target is not None:
            binding.initialize_all_targets()
            binding.initialize_all_asmprinters()
            machine = binding.Target.from_triple(target)
        else:
            machine = binding.Target.from_default_triple()

        if jit:
            return machine.create_target_machine(opt=opt)

        return machine.create_target_machine(opt=opt, reloc="pic",
                                             codemodel="small")
    except RuntimeError as error:
        raise TargetError(f"cannot use target {requested!r}: {error}") from None


def validate_target(target: str | None) -> None:
    """Ensure that an explicitly requested target can generate code."""
    if target is not None:
        _target_machine(target)


def _optimize_module(machine, llvm_module, opt: int) -> None:
    """Apply the compiler's optimization policy to one verified LLVM module."""
    options = binding.create_pipeline_tuning_options(speed_level=opt)
    pass_builder = binding.create_pass_builder(machine, options)

    if opt > 0:
        pass_builder.getModulePassManager().run(llvm_module, pass_builder)
    else:
        manager = binding.ModulePassManager()
        manager.add_always_inliner_pass()
        manager.run(llvm_module, pass_builder)


def prepare_module(module: ir.Module, opt: int = 0, target: str | None = None,
                   jit: bool = False) -> tuple:
    """
    Verify an LLVM module against its target - the host, or the triple
    given - returning the target machine and the module round-tripped
    through the LLVM binding.

    An optimization level above 0 runs LLVM's standard pass pipeline over
    the module, cc-style: -O1 through -O3.

    Ahead-of-time output is position-independent small-code-model code, as
    'cc' expects when linking a PIE; the JIT keeps LLVM's JIT defaults,
    whose large code model tolerates objects landing anywhere in memory.
    """
    machine = _target_machine(target, opt, jit)
    module.triple = machine.triple

    # round-trip the IR through the LLVM binding and verify it
    llvm_module = binding.parse_assembly(str(module))
    llvm_module.verify()

    # '@inline' functions inline even unoptimized: the standard pipeline
    # honors 'alwaysinline' on its own, but -O0 runs only that pass.
    _optimize_module(machine, llvm_module, opt)

    return machine, llvm_module


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
        validate_target(target)
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

    # the unversioned name can be a linker script (glibc's libm.so, say),
    # which the dynamic loader rejects; find_library knows the sonamed file
    if (found := ctypes.util.find_library(name)) is not None:
        candidates.append(found)

    for candidate in [*candidates, filename]:
        try:
            binding.load_library_permanently(candidate)
            return
        except RuntimeError:
            continue

    raise NameError(f"cannot load library {name!r}")


def _object_error(path: str, detail: str) -> ObjectFormatError:
    """Build one consistently formatted native-input diagnostic."""
    return ObjectFormatError(f"{path!r} is not a valid object file: {detail}")


def validate_object_data(data: bytes, path: str) -> None:
    """
    Reject truncated or unrecognized native objects before LLVM parses them.

    The JIT only accepts host objects, whose common containers are ELF, Mach-O,
    and COFF. This intentionally validates the outer structure rather than
    attempting to duplicate LLVM's complete relocation and section parser; JIT
    isolation remains the final boundary for a structurally valid hostile file.
    """
    if len(data) < 4:
        raise _object_error(path, "the file is too short")

    if data.startswith(b"\x7fELF"):
        if len(data) < 16:
            raise _object_error(path, "the ELF identification is truncated")

        word_size, byte_order, version = data[4], data[5], data[6]
        if word_size not in (1, 2):
            raise _object_error(path, "the ELF word size is invalid")
        if byte_order not in (1, 2):
            raise _object_error(path, "the ELF byte order is invalid")
        if version != 1:
            raise _object_error(path, "the ELF version is invalid")

        header_size = 52 if word_size == 1 else 64
        if len(data) < header_size:
            raise _object_error(path, "the ELF header is truncated")

        order = "little" if byte_order == 1 else "big"
        if word_size == 1:
            encoded_header_size = int.from_bytes(data[40:42], order)
            section_offset = int.from_bytes(data[32:36], order)
            section_entry_size = int.from_bytes(data[46:48], order)
            section_count = int.from_bytes(data[48:50], order)
            minimum_section_size = 40
        else:
            encoded_header_size = int.from_bytes(data[52:54], order)
            section_offset = int.from_bytes(data[40:48], order)
            section_entry_size = int.from_bytes(data[58:60], order)
            section_count = int.from_bytes(data[60:62], order)
            minimum_section_size = 64

        if encoded_header_size < header_size or encoded_header_size > len(data):
            raise _object_error(path, "the ELF header size is invalid")
        if section_count:
            if section_entry_size < minimum_section_size:
                raise _object_error(path, "the ELF section entry size is invalid")
            if (section_offset > len(data)
                    or section_count > (len(data) - section_offset) // section_entry_size):
                raise _object_error(path, "the ELF section table is truncated")
        return

    magic = data[:4]
    macho = {
        b"\xce\xfa\xed\xfe": ("little", 28),
        b"\xcf\xfa\xed\xfe": ("little", 32),
        b"\xfe\xed\xfa\xce": ("big", 28),
        b"\xfe\xed\xfa\xcf": ("big", 32),
    }
    if magic in macho:
        order, header_size = macho[magic]
        if len(data) < header_size:
            raise _object_error(path, "the Mach-O header is truncated")
        command_size = int.from_bytes(data[20:24], order)
        if command_size > len(data) - header_size:
            raise _object_error(path, "the Mach-O load commands are truncated")
        return

    fat_macho = {
        b"\xca\xfe\xba\xbe": ("big", 20),
        b"\xbe\xba\xfe\xca": ("little", 20),
        b"\xca\xfe\xba\xbf": ("big", 32),
        b"\xbf\xba\xfe\xca": ("little", 32),
    }
    if magic in fat_macho:
        order, entry_size = fat_macho[magic]
        if len(data) < 8:
            raise _object_error(path, "the universal Mach-O header is truncated")
        architectures = int.from_bytes(data[4:8], order)
        if architectures > (len(data) - 8) // entry_size:
            raise _object_error(path, "the universal Mach-O table is truncated")
        for index in range(architectures):
            start = 8 + index * entry_size
            width = 8 if entry_size == 32 else 4
            offset = int.from_bytes(data[start + 8:start + 8 + width], order)
            size = int.from_bytes(
                data[start + 8 + width:start + 8 + 2 * width], order)
            if offset > len(data) or size > len(data) - offset:
                raise _object_error(path, "a universal Mach-O slice is truncated")
        return

    # COFF has no file magic. Its machine field and bounded section table
    # distinguish supported object files from arbitrary data.
    coff_machines = {
        0x014C,  # i386
        0x01C0, 0x01C2, 0x01C4,  # ARM
        0x8664,  # x86-64
        0xAA64,  # ARM64
        0x5032, 0x5064, 0x5128,  # RISC-V
    }
    if len(data) >= 20 and int.from_bytes(data[:2], "little") in coff_machines:
        sections = int.from_bytes(data[2:4], "little")
        optional_size = int.from_bytes(data[16:18], "little")
        table_offset = 20 + optional_size
        if table_offset > len(data) or sections > (len(data) - table_offset) // 40:
            raise _object_error(path, "the COFF section table is truncated")
        return

    raise _object_error(path, "the format is not recognized")


def archive_members(path: str) -> list[tuple[str, bytes]]:
    """Read and validate every native object member of a Unix archive."""
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise ObjectFormatError(f"cannot read archive {path!r}: {error}") from None

    if data[:8] != b"!<arch>\n":
        raise ObjectFormatError(f"{path!r} is not a valid static library: "
                                "the archive signature is missing")

    members = []
    position = 8
    while position < len(data):
        if len(data) - position < 60:
            raise ObjectFormatError(f"{path!r} is not a valid static library: "
                                    "a member header is truncated")

        header = data[position:position + 60]
        position += 60
        if header[58:60] != b"`\n":
            raise ObjectFormatError(f"{path!r} is not a valid static library: "
                                    "a member header has an invalid trailer")

        name = header[:16].decode("ascii", "replace").strip()
        size_text = header[48:58].strip()
        if not size_text or not size_text.isdigit():
            raise ObjectFormatError(f"{path!r} is not a valid static library: "
                                    "a member size is invalid")
        size = int(size_text)
        if size > len(data) - position:
            raise ObjectFormatError(f"{path!r} is not a valid static library: "
                                    "a member is truncated")

        content = data[position:position + size]
        position += size
        if size & 1:
            if position >= len(data):
                raise ObjectFormatError(
                    f"{path!r} is not a valid static library: "
                    "a member's alignment byte is missing")
            position += 1

        # A BSD long name rides at the front of the member's data.
        if name.startswith("#1/"):
            length_text = name[3:]
            if not length_text.isdigit():
                raise ObjectFormatError(
                    f"{path!r} is not a valid static library: "
                    "a BSD member name length is invalid")
            length = int(length_text)
            if length > len(content):
                raise ObjectFormatError(
                    f"{path!r} is not a valid static library: "
                    "a BSD member name is truncated")
            name = content[:length].decode("ascii", "replace").rstrip("\0")
            content = content[length:]

        # Symbol tables and GNU's long-name table describe members; they are
        # not themselves objects. A '/<n>' spelling is a real member whose
        # name is stored in the GNU table.
        if (name in ("/", "//", "/SYM64/")
                or name.startswith("__.SYMDEF")):
            continue

        label = f"{path}({name.rstrip('/') or '<unnamed>'})"
        validate_object_data(content, label)
        members.append((name, content))

    return members


def add_object(engine, path: str) -> None:
    """Validate and add the exact bytes read from one object file."""
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise ObjectFormatError(f"cannot read object file {path!r}: {error}") from None

    validate_object_data(data, path)
    try:
        engine.add_object_file(binding.ObjectFileRef.from_data(data))
    except RuntimeError as error:
        raise _object_error(path, str(error)) from None


def add_archive(engine, path: str) -> None:
    """
    Load a static library's members into the JIT engine: the archive is
    unpacked in place, each object joining like one given directly.

    Both archive flavors are read: GNU's, with its '//' long-name table,
    and BSD's, with '#1/<n>' names prefixed to the member's data.
    """
    for name, content in archive_members(path):
        try:
            engine.add_object_file(binding.ObjectFileRef.from_data(content))
        except RuntimeError as error:
            label = f"{path}({name.rstrip('/') or '<unnamed>'})"
            raise _object_error(label, str(error)) from None


def _jit_entry_abi(module: ir.Module) -> str:
    """Return the validated native shape of the module's defined entry point."""
    entry = module.globals.get("main")
    if not isinstance(entry, ir.Function) or not entry.blocks:
        raise NameError("program has no 'main' function definition")

    function_type = entry.function_type
    i32 = ir.IntType(32)
    char_pointer_pointer = ir.PointerType(ir.PointerType(ir.IntType(8)))
    params = tuple(function_type.args)

    if (function_type.return_type != i32 or function_type.var_arg):
        raise TypeError("JIT entry 'main' has an invalid ABI")
    if params == ():
        return "none"
    if params == (i32, char_pointer_pointer):
        return "args"
    raise TypeError("JIT entry 'main' has an invalid ABI")


def _run_jit_in_process(llvm_ir: str, entry_abi: str, argv: list[str],
                        objects: list[str], libs: list[str],
                        lib_dirs: list[str], opt: int) -> int:
    """The isolated worker's LLVM and native-code execution path."""
    target_machine = _target_machine(opt=opt, jit=True)
    llvm_module = binding.parse_assembly(llvm_ir)
    llvm_module.triple = target_machine.triple
    llvm_module.verify()

    _optimize_module(target_machine, llvm_module, opt)

    for name in libs:
        load_library(name, lib_dirs)

    with binding.create_mcjit_compiler(llvm_module, target_machine) as engine:
        for path in objects:
            if str(path).endswith(".a"):
                add_archive(engine, path)
            else:
                add_object(engine, path)

        engine.finalize_object()
        engine.run_static_constructors()

        address = engine.get_function_address("main")
        if not address:
            raise NameError("program has no 'main' function")

        if entry_abi == "none":
            c_main = ctypes.CFUNCTYPE(ctypes.c_int32)(address)
            code = c_main()
        elif entry_abi == "args":
            c_argv = (ctypes.c_char_p * (len(argv) + 1))(
                *[a.encode() for a in argv], None)
            c_main = ctypes.CFUNCTYPE(
                ctypes.c_int32, ctypes.c_int32,
                ctypes.POINTER(ctypes.c_char_p))(address)
            code = c_main(len(argv), c_argv)
        else:
            raise TypeError("JIT entry 'main' has an invalid ABI descriptor")
        engine.run_static_destructors()
        ctypes.CDLL(None).fflush(None)
        return code


def _jit_worker(connection, llvm_ir: str, entry_abi: str, argv: list[str],
                objects: list[str], libs: list[str], lib_dirs: list[str],
                opt: int) -> None:
    """Run one JIT request and return only structured status to the parent."""
    try:
        code = _run_jit_in_process(
            llvm_ir, entry_abi, argv, objects, libs, lib_dirs, opt)
        connection.send(("ok", code))
    except Exception as error:
        if isinstance(error, ObjectFormatError):
            kind = "object"
        elif isinstance(error, NameError):
            kind = "name"
        elif isinstance(error, TargetError):
            kind = "target"
        else:
            kind = "backend"
        connection.send(("error", kind, str(error)))
    finally:
        connection.close()


def run_jit(module: ir.Module, argv: list[str], objects: list[str] = (),
            libs: list[str] = (), lib_dirs: list[str] = (), opt: int = 0) -> int:
    """
    JIT-compile a module in an isolated process and return its exit code.

    Extra object files are loaded into the engine, and '-l' libraries into
    the worker, their symbols resolvable from the program like any linked code.
    A malformed native input or generated-code fault can terminate the worker,
    but cannot corrupt or crash the compiler host.
    """
    entry_abi = _jit_entry_abi(module)

    # 'fork' avoids re-importing an embedding application's __main__ on POSIX;
    # Windows has no fork and uses a fully serializable spawn request instead.
    method = "spawn" if sys.platform == "win32" else "fork"
    context = multiprocessing.get_context(method)
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=_jit_worker,
        args=(send, str(module), entry_abi, list(argv), list(objects),
              list(libs), list(lib_dirs), opt),
    )

    started = False
    try:
        process.start()
        started = True
        send.close()
        process.join()
        exitcode = process.exitcode
        try:
            message = receive.recv() if receive.poll() else None
        except EOFError:
            # A signal may close the worker's pipe without a complete frame.
            message = None
    finally:
        send.close()
        receive.close()
        if started:
            if process.is_alive():
                process.terminate()
                process.join()
            process.close()

    if message is not None:
        if message[0] == "ok":
            return message[1]

        _, kind, detail = message
        if kind == "object":
            raise ObjectFormatError(detail)
        if kind == "name":
            raise NameError(detail)
        if kind == "target":
            raise TargetError(detail)
        raise OSError(f"JIT worker failed: {detail}")

    if exitcode is not None and exitcode < 0:
        signum = -exitcode
        description = signal.strsignal(signum) or f"signal {signum}"
        raise OSError(f"JIT worker terminated by {description}")

    # A program that deliberately calls exit() cannot send a normal response;
    # preserve its chosen process status. Other Python/backend failures are
    # caught and sent above.
    if exitcode is not None and 0 <= exitcode <= 255:
        return exitcode

    raise OSError(f"JIT worker terminated with exit code {exitcode}")


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
