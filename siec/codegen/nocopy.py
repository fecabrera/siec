"""Copy restrictions for explicitly moved values, without automatic cleanup."""

from siec.codegen.types import is_reference, raw_array, sized_array, strip_const


def noncopyable(gen, type_name, seen=frozenset()):
    """Inspect already resolved types, including fields from imported modules."""
    gen.ungated_types += 1
    try:
        return _noncopyable(gen, type_name, seen)
    finally:
        gen.ungated_types -= 1


def _noncopyable(gen, type_name, seen):
    """Check stored fields and owning generic arguments, never borrowed pointers."""
    from siec.codegen.aliases import expand_alias
    from siec.codegen.generics import split_generic
    from siec.codegen.ownership import destroyable

    if not type_name or is_reference(type_name):
        return False
    name = strip_const(expand_alias(gen, type_name))
    if is_reference(name) or name.endswith(('*', '[]')):
        return False
    raw = raw_array(name)
    if raw:
        return noncopyable(gen, raw[0], seen)
    array = sized_array(name)
    if array:
        return noncopyable(gen, array[0].removesuffix('[]'), seen)
    if name in seen:
        return False
    seen = seen | {name}
    info = gen.structs.get(name)
    if info and (info.nocopy or any(
            noncopyable(gen, field.type, seen) for field in info.fields or ())):
        return True
    # Owning containers can store their elements behind pointers. Checking only
    # inline fields would miss List<File>, Queue<File>, and Map<K, File>.
    generic = split_generic(name)
    return bool(generic and any(noncopyable(gen, arg, seen)
                                for arg in generic[1])
                and destroyable(gen, name))


def check_copy(gen, expr, type_name, scope):
    """Reject named or borrowed sources; fresh results have no previous owner."""
    from siec.ast import (Var, Member, Index, Cast, UnaryOp, Move, Ternary)
    from siec.codegen.ownership import expression_returns_reference

    if not noncopyable(gen, type_name):
        return
    if isinstance(expr, (Move, Ternary)):
        return
    if getattr(expr, 'self_transfer', False):
        return
    if (isinstance(expr, (Var, Member, Index, Cast, UnaryOp))
            or expression_returns_reference(gen, expr)):
        raise TypeError(
            f"cannot copy @nocopy value of type {strip_const(type_name)!r}; "
            "use 'move' on an owned local or call clone() explicitly")
