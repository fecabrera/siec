"""Resolution of declaration headers under lexical type parameters."""

import re

from siec.ast import Program
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator
from siec.codegen.type_refs import TypeRef, parse_type_ref
from siec.codegen.types import (
    SCALAR_TYPES,
    is_reference,
)

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def has_parameter(spelling: str, parameters: frozenset[str]) -> bool:
    """Whether a type spelling contains one of its lexical placeholders."""
    return any(match.group() in parameters for match in IDENT.finditer(spelling))


def imported_base(gen: CodeGenerator, base: str) -> str:
    """Resolve a qualified or member-imported type base."""
    if "." in base:
        resolved = gen.resolve_qualified(base.split("."))
        if resolved is None:
            raise TypeError(f"unknown type {base!r}")
        return resolved

    return gen.resolve_type_symbol(base)


def generic_template(gen: CodeGenerator, base: str, arity: int):
    """The generic struct template selected by a written argument count."""
    template = gen.generic_structs.get(base)
    if template is not None and len(template.params) != arity:
        template = gen.generic_structs.get(f"{base}#{arity}")
    return template


def resolve_header_type(gen: CodeGenerator, spelling: str | None,
                        parameters=frozenset(), *,
                        allow_interface: bool = False,
                        allow_opaque: bool = False,
                        allow_free: bool = False) -> str | None:
    """
    Resolve one type annotation without substituting its lexical parameters.

    Generic shapes containing placeholders validate their bases, arities, and
    nested arguments without creating a concrete instance. Fully concrete
    shapes use normal alias expansion, including bound checking and generic
    instantiation.
    """
    if spelling is None:
        return None

    parameters = frozenset(parameters)
    ref = parse_type_ref(spelling)

    if ref.kind == "const":
        inner = resolve_header_type(
            gen,
            ref.inner.spelling(),
            parameters,
            allow_interface=allow_interface,
            allow_opaque=allow_opaque,
            allow_free=allow_free,
        )
        inner_ref = parse_type_ref(inner)
        return (inner if inner_ref.kind == "const"
                else TypeRef("const", inner=inner_ref).spelling())

    if ref.kind == "reference":
        inner = resolve_header_type(
            gen,
            ref.inner.spelling(),
            parameters,
            allow_interface=allow_interface,
            allow_opaque=allow_opaque,
            allow_free=allow_free,
        )
        return TypeRef(
            "reference", inner=parse_type_ref(inner)).spelling()

    if ref.kind == "nonnull":
        inner = resolve_header_type(
            gen,
            ref.inner.spelling(),
            parameters,
            allow_interface=allow_interface,
            allow_opaque=allow_opaque,
            allow_free=allow_free,
        )
        inner_ref = parse_type_ref(inner)
        if inner_ref.kind != "pointer":
            raise TypeError("'!' can only qualify a pointer type")
        return TypeRef("nonnull", inner=inner_ref).spelling()

    if ref.kind in ("function", "closure"):
        resolved = ",".join(
            resolve_header_type(
                gen,
                param.spelling(),
                parameters,
                allow_interface=allow_interface,
                allow_free=allow_free,
            )
            for param in ref.items
        )
        result = f"{'closure ' if ref.kind == 'closure' else ''}fn({resolved})"
        if ref.result is not None:
            result += "->" + resolve_header_type(
                gen,
                ref.result.spelling(),
                parameters,
                allow_interface=allow_interface,
                allow_free=allow_free,
            )
        return result

    if ref.kind in ("struct", "union"):
        resolved = [
            (name, resolve_header_type(
                gen,
                type_.spelling(),
                parameters,
                allow_interface=allow_interface,
                allow_free=allow_free,
            ))
            for name, type_ in ref.fields
        ]
        kind = ref.kind
        result = kind + "{" + ";".join(
            f"{name}:{type_}" for name, type_ in resolved) + "}"
        return result

    if ref.kind == "raw":
        from siec.codegen.enums import evaluate_size

        element = resolve_header_type(
            gen,
            ref.inner.spelling(),
            parameters,
            allow_interface=allow_interface,
            allow_free=allow_free,
        )
        size = (ref.size if ref.size.isdigit()
                else str(evaluate_size(gen, ref.size)))
        return f"raw<{element}>[{size}]"

    if ref.kind == "sized":
        from siec.codegen.enums import evaluate_size

        element = resolve_header_type(
            gen,
            ref.inner.spelling(),
            parameters,
            allow_interface=allow_interface,
            allow_opaque=True,
            allow_free=allow_free,
        )
        size = (ref.size if ref.size.isdigit()
                else str(evaluate_size(gen, ref.size)))
        return f"{element}[{size}]"

    if ref.kind == "array":
        element = resolve_header_type(
            gen,
            ref.inner.spelling(),
            parameters,
            allow_interface=allow_interface,
            allow_opaque=True,
            allow_free=allow_free,
        )
        return TypeRef(
            "array", inner=parse_type_ref(element)).spelling()

    if ref.kind == "pointer":
        base = resolve_header_type(
            gen,
            ref.inner.spelling(),
            parameters,
            allow_interface=allow_interface,
            allow_opaque=True,
            allow_free=allow_free,
        )
        base_ref = parse_type_ref(base)
        if base_ref.kind in ("const", "reference"):
            raise TypeError(f"cannot derive {spelling!r}: its target "
                            f"{base!r} carries a modifier")
        return TypeRef("pointer", inner=base_ref).spelling()

    if ref.kind == "generic":
        base = imported_base(gen, ref.name)
        interface = base in gen.interfaces
        args = [
            resolve_header_type(
                gen,
                arg.spelling(),
                parameters,
                allow_interface=allow_interface,
                # A type argument names a type identity; whether it needs a
                # representation is decided by the template. Generic structs
                # validate their substituted fields, while aliases may erase
                # a phantom argument entirely.
                allow_opaque=True,
                allow_free=allow_free or interface,
            )
            for arg in ref.items
        ]
        rebuilt = f"{base}<{','.join(args)}>"

        if base in gen.interfaces:
            if not allow_interface:
                raise TypeError(f"interface {base!r} is not a concrete type: "
                                "only a parameter can take an interface, "
                                "standing for any struct implementing it")
            iface = gen.interfaces[base]
            expected = len(iface.params or ())
            if len(args) != expected:
                raise TypeError(f"interface {base!r} takes {expected} type "
                                f"argument{'s' if expected != 1 else ''}, "
                                f"got {len(args)}")
            return rebuilt

        if base == "Tuple":
            if not args:
                raise TypeError(
                    "a Tuple needs its element types: 'Tuple<A, B, ...>'")
            return rebuilt

        alias = gen.generic_aliases.get(base)
        template = generic_template(gen, base, len(args))
        owner = alias or template
        if owner is None:
            raise TypeError(f"unknown type {base!r}")

        expected = len(owner.params)
        if len(args) != expected:
            kind = "type alias" if alias is not None else "struct"
            raise TypeError(f"generic {kind} {base!r} takes {expected} type "
                            f"argument{'s' if expected != 1 else ''}, "
                            f"got {len(args)}")

        if has_parameter(rebuilt, parameters):
            return rebuilt

        from siec.codegen.aliases import expand_alias

        concrete = expand_alias(gen, rebuilt, checked=False)
        if concrete == rebuilt:
            return rebuilt
        return resolve_header_type(
            gen,
            concrete,
            parameters,
            allow_interface=allow_interface,
            allow_opaque=allow_opaque,
            allow_free=allow_free,
        )

    if ref.kind != "name":
        raise TypeError(f"unknown type {spelling!r}")
    base = imported_base(gen, ref.name)
    if base in parameters:
        return base

    if base in gen.aliases:
        from siec.codegen.aliases import expand_alias

        return resolve_header_type(
            gen,
            expand_alias(gen, base, checked=False),
            parameters,
            allow_interface=allow_interface,
            allow_opaque=allow_opaque,
            allow_free=allow_free,
        )

    if base in gen.interfaces:
        if allow_interface:
            iface = gen.interfaces[base]
            expected = len(iface.params or ())
            if expected:
                raise TypeError(f"interface {base!r} takes {expected} type "
                                f"argument{'s' if expected != 1 else ''}, "
                                "got 0")
            return base
        raise TypeError(f"interface {base!r} is not a concrete type: "
                        "only a parameter can take an interface, "
                        "standing for any struct implementing it")

    if base == "opaque":
        if not allow_opaque:
            raise TypeError("'opaque' can only be used as a pointer (opaque*)")
        return base

    if base in SCALAR_TYPES:
        return base

    if base in gen.structs:
        info = gen.structs[base]
        if info.fields is None and not allow_opaque:
            raise TypeError(f"struct {base!r} has no body and can only be "
                            f"used through a pointer ({base}*)")
        return base

    if base in gen.generic_structs or base in gen.generic_aliases:
        raise TypeError(f"generic type {base!r} needs type arguments")

    if allow_free and base.isidentifier():
        return base

    raise TypeError(f"unknown type {base!r}")


def resolve_bound(gen: CodeGenerator, spelling: str,
                  parameters: frozenset[str]) -> str:
    """Resolve one generic bound, accepting interfaces and concrete types."""
    from siec.codegen.interfaces import expand_bound
    from siec.codegen.generics import split_generic

    canonical, is_interface = expand_bound(gen, spelling)
    if (is_interface and split_generic(canonical) is None
            and canonical in gen.interfaces):
        # A bare generic interface is an existential bound: 'Iterable'
        # accepts any 'Iterable<T>' claim, with T inferred from that claim.
        return canonical
    return resolve_header_type(
        gen,
        canonical,
        parameters,
        allow_interface=is_interface,
    )


def resolve_constraints(gen: CodeGenerator, owner,
                        parameters: frozenset[str],
                        attribute: str = "constraints") -> None:
    """Resolve one declaration's bound map without rewriting its syntax."""
    constraints = getattr(owner, attribute) or {}
    unknown = set(constraints) - parameters
    if unknown:
        name = sorted(unknown)[0]
        raise TypeError(f"bound names undeclared type parameter {name!r}")

    for value in constraints.values():
        bounds = value if isinstance(value, tuple) else (value,)
        for bound in bounds:
            resolve_bound(gen, bound, parameters)


def resolve_claim(gen: CodeGenerator, spelling: str,
                  parameters: frozenset[str]) -> str:
    """Resolve a claimed interface spelling and validate its arity."""
    return resolve_header_type(
        gen,
        spelling,
        parameters,
        allow_interface=True,
    )


def resolve_type_declaration_headers(gen: CodeGenerator,
                                     program: Program) -> None:
    """Resolve every generic alias, struct, interface, and extension header."""
    for alias in program.aliases:
        if alias.params is None:
            continue
        with source_location(line=alias.line, file=alias.file):
            gen.current_file = alias.file
            parameters = frozenset(alias.params)
            resolve_constraints(gen, alias, parameters)
            # An alias may name an opaque foreign handle. The alias itself
            # owns no storage; each use still has to put that handle behind
            # indirection before ordinary type validation accepts it.
            resolve_header_type(
                gen,
                alias.type,
                parameters,
                allow_opaque=True,
            )

    for struct in program.structs:
        if struct.params is None and not struct.is_interface:
            continue
        with source_location(line=struct.line, file=struct.file):
            gen.current_file = struct.file
            parameters = frozenset(struct.params or ())
            resolve_constraints(gen, struct, parameters)
            for field in struct.fields or ():
                resolve_header_type(
                    gen,
                    field.type,
                    parameters,
                )
                if is_reference(field.type):
                    raise TypeError(
                        f"field {field.name!r} cannot be a reference")
            for claim in struct.interfaces or ():
                resolve_claim(gen, claim, parameters)

    for ext in program.extends:
        if ext.params is None:
            continue
        with source_location(line=ext.line, file=ext.file):
            gen.current_file = ext.file
            parameters = frozenset(ext.params)
            resolve_constraints(gen, ext, parameters)
            resolve_header_type(gen, ext.name, parameters)
            for claim in ext.interfaces:
                resolve_claim(gen, claim, parameters)


def resolve_callable_header(gen: CodeGenerator, fn, *,
                            interface_action: bool = False) -> None:
    """Resolve one callable signature under its receiver and own parameters."""
    with source_location(line=fn.line, file=fn.file):
        gen.current_file = fn.file
        parameters = frozenset(
            [*(fn.receiver_params or ()), *(fn.type_params or ())]
        )
        if interface_action:
            parameters = parameters | {"Self"}
        resolve_constraints(gen, fn, parameters)
        resolve_constraints(
            gen,
            fn,
            parameters,
            attribute="receiver_constraints",
        )

        if fn.receiver is not None and not interface_action:
            receiver = fn.receiver
            receiver_ref = parse_type_ref(receiver)
            if (fn.receiver_params is not None
                    and receiver not in fn.receiver_params
                    and receiver_ref.kind not in ("generic", "array")):
                receiver += f"<{','.join(fn.receiver_params)}>"
            resolve_header_type(gen, receiver, parameters)

        if interface_action:
            from siec.codegen.interfaces import takes_self

            start = 1 if takes_self(fn) else 0
        else:
            start = 0
        for index, param in enumerate(fn.params):
            if index < start:
                continue
            resolve_header_type(
                gen,
                param.type,
                parameters,
                allow_interface=interface_action,
            )
        resolve_header_type(
            gen,
            fn.return_type,
            parameters,
            allow_interface=interface_action,
        )

        # Nested closures belong to this callable's source. Resolve their
        # signatures here so Check infers and validates already-canonical
        # types instead of expanding aliases while checking bodies.
        from siec.codegen.closures import resolve_nested_closures

        resolve_nested_closures(gen, fn.body, parameters)
        for param in fn.params:
            resolve_nested_closures(gen, param.default, parameters)
