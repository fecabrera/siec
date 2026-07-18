"""Compile-time size computation for 'sizeof'."""

from llvmlite import ir

from siec.codegen.aliases import expand_alias
from siec.codegen.generator import CodeGenerator
from siec.codegen.types import resolve_type, strip_reference

# the host target's data layout, created once on first use
_target_data = None


def host_target_data():
    """
    The ABI layout rules of the host target, deciding every type's size.
    """
    global _target_data
    if _target_data is None:
        from llvmlite import binding

        binding.initialize_native_target()
        binding.initialize_native_asmprinter()
        _target_data = (binding.Target.from_default_triple()
                        .create_target_machine().target_data)

    return _target_data


def size_of(gen: CodeGenerator, name: str, scope: dict | None = None) -> int:
    """
    The size in bytes of a type name, or of a variable's declared type when
    the name is one in scope (or a global); a '&T' parameter measures its T.
    """
    if scope is not None and name in scope:
        name = strip_reference(scope[name].type)
    elif (symbol := gen.resolve_symbol(name)) in gen.globals:
        name = gen.globals[symbol]

    name = expand_alias(gen, name)
    resolved = resolve_type(name, gen.structs)

    if isinstance(resolved, ir.VoidType):
        raise TypeError("'sizeof' needs a sized type, not void")

    size = resolved.get_abi_size(host_target_data(), context=gen.module.context)

    # an '@align(N)' struct pads to its alignment, so arrays of it stay aligned
    if (align := gen.struct_align(name)) is not None:
        size = -(-size // align) * align

    return size
