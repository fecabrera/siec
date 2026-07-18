"""Registration of '@extern let' and '@static let' global variables."""

from llvmlite import ir

from siec.ast import BoolLiteral, FloatLiteral, Global, Program, StrLiteral
from siec.codegen.enums import evaluate
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator
from siec.codegen.types import is_const, is_reference, resolve_type, sized_array, strip_const


def register_globals(gen: CodeGenerator, program: Program) -> None:
    """
    Declare every module-level variable: '@extern let' as external linkage,
    its storage defined and initialized outside this program; '@static let'
    as file-local storage defined here, under a mangled symbol its own file
    resolves to, like a static function's.
    """
    for glob in program.globals:
        with source_location(line=glob.line, file=glob.file):
            symbol = glob.name
            if glob.is_static:
                key = (glob.file, glob.name)
                if key in gen.statics:
                    raise TypeError(f"global {glob.name!r} is declared more than once")

                gen.statics[key] = symbol = f"{glob.name}.static.{len(gen.statics)}"

            if symbol in gen.globals or symbol in gen.module.globals:
                raise TypeError(f"global {glob.name!r} is declared more than once")

            if is_reference(glob.type):
                raise TypeError("a reference cannot type a variable")

            var = ir.GlobalVariable(gen.module, resolve_type(glob.type, gen.structs),
                                    name=symbol)

            if glob.is_static:
                var.linkage = "internal"
                var.initializer = global_initializer(gen, glob, symbol)
            else:
                var.linkage = "external"

            # a sized array declares an 'X[]', the size only directing its
            # backing — the same canonical type a local declaration records
            sie_type = glob.type
            if (sized := sized_array(strip_const(sie_type))) is not None:
                sie_type = f"const {sized[0]}" if is_const(sie_type) else sized[0]

            gen.globals[symbol] = sie_type


def global_initializer(gen: CodeGenerator, glob: Global, symbol: str) -> ir.Constant:
    """
    Build a static global's initial value: zero when none is given, a
    compile-time constant otherwise.
    """
    type_ = resolve_type(glob.type, gen.structs)

    # a sized array 'X[N]' points at N zeroed elements of module storage,
    # its length N — the module-level shape of a local sized declaration
    if (sized := sized_array(strip_const(glob.type))) is not None:
        if glob.value is not None:
            raise TypeError(f"a sized array takes its contents from its size; "
                            f"initialize an {sized[0]!r} instead")

        element = type_.elements[0].pointee
        backing = ir.GlobalVariable(gen.module, ir.ArrayType(element, sized[1]),
                                    name=f"{symbol}.backing")
        backing.linkage = "internal"
        backing.initializer = ir.Constant(backing.value_type, None)

        zero = ir.Constant(ir.IntType(32), 0)
        return ir.Constant(type_, [backing.gep([zero, zero]),
                                   ir.Constant(ir.IntType(64), sized[1])])

    if glob.value is None:
        return ir.Constant(type_, None)  # zero-initialized, C-style

    if isinstance(glob.value, FloatLiteral):
        return ir.Constant(type_, glob.value.value)

    if isinstance(glob.value, BoolLiteral):
        return ir.Constant(type_, 1 if glob.value.value else 0)

    # a string initializer points the global at a private string constant
    if isinstance(glob.value, StrLiteral):
        if strip_const(glob.type) != "char*":
            raise TypeError(f"cannot initialize a {glob.type!r} global with a string")

        return string_constant(gen, glob.value.value).bitcast(type_)

    # anything else must evaluate to an integer at compile time
    return ir.Constant(type_, evaluate(gen, glob.value))


def string_constant(gen: CodeGenerator, text: str) -> ir.GlobalVariable:
    """
    Store a string's bytes as a private, null-terminated module constant.
    """
    data = text.encode() + b"\0"
    array_type = ir.ArrayType(ir.IntType(8), len(data))

    const = ir.GlobalVariable(gen.module, array_type, name=f".str.{gen.str_count}")
    const.global_constant = True
    const.linkage = "private"
    const.initializer = ir.Constant(array_type, bytearray(data))

    gen.str_count += 1
    return const
