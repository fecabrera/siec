"""Resolution of declaration headers under lexical type parameters."""

import re

from siec.ast import Program
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator
from siec.codegen.types import (
    SCALAR_TYPES,
    anonymous_struct,
    fn_type_parts,
    is_reference,
    raw_array,
    sized_array,
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

    bound = gen.member_bindings.get((gen.current_file, base))
    if bound is not None:
        return bound

    return base


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

    if spelling.startswith("const "):
        inner = resolve_header_type(
            gen,
            spelling.removeprefix("const "),
            parameters,
            allow_interface=allow_interface,
            allow_opaque=allow_opaque,
            allow_free=allow_free,
        )
        return inner if inner.startswith("const ") else f"const {inner}"

    if spelling.startswith("&"):
        inner = resolve_header_type(
            gen,
            spelling[1:],
            parameters,
            allow_interface=allow_interface,
            allow_opaque=allow_opaque,
            allow_free=allow_free,
        )
        return f"&{inner}"

    if spelling.startswith("fn("):
        params, ret, suffix = fn_type_parts(spelling)
        resolved = ",".join(
            resolve_header_type(
                gen,
                param,
                parameters,
                allow_interface=allow_interface,
                allow_free=allow_free,
            )
            for param in params
        )
        result = f"fn({resolved})"
        if ret is not None:
            result += "->" + resolve_header_type(
                gen,
                ret,
                parameters,
                allow_interface=allow_interface,
                allow_free=allow_free,
            )
        return result + suffix

    if (anonymous := anonymous_struct(spelling)) is not None:
        is_union, fields, suffix = anonymous
        resolved = [
            (name, resolve_header_type(
                gen,
                type_,
                parameters,
                allow_interface=allow_interface,
                allow_free=allow_free,
            ))
            for name, type_ in fields
        ]
        kind = "union" if is_union else "struct"
        result = kind + "{" + ";".join(
            f"{name}:{type_}" for name, type_ in resolved) + "}"
        return result + suffix

    if (raw := raw_array(spelling)) is not None:
        from siec.codegen.enums import evaluate_size

        element, size, suffix = raw
        element = resolve_header_type(
            gen,
            element,
            parameters,
            allow_interface=allow_interface,
            allow_free=allow_free,
        )
        if not size.isdigit():
            size = str(evaluate_size(gen, size))
        return f"raw<{element}>[{size}]{suffix}"

    if (sized := sized_array(spelling)) is not None:
        from siec.codegen.enums import evaluate_size

        array, size = sized
        element = resolve_header_type(
            gen,
            array[:-2],
            parameters,
            allow_interface=allow_interface,
            allow_opaque=True,
            allow_free=allow_free,
        )
        if not size.isdigit():
            size = str(evaluate_size(gen, size))
        return f"{element}[{size}]"

    if spelling.endswith("[]"):
        element = resolve_header_type(
            gen,
            spelling[:-2],
            parameters,
            allow_interface=allow_interface,
            allow_opaque=True,
            allow_free=allow_free,
        )
        return f"{element}[]"

    if spelling.endswith("*"):
        base = resolve_header_type(
            gen,
            spelling[:-1],
            parameters,
            allow_interface=allow_interface,
            allow_opaque=True,
            allow_free=allow_free,
        )
        if base.startswith("const ") or base.startswith("&"):
            raise TypeError(f"cannot derive {spelling!r}: its target "
                            f"{base!r} carries a modifier")
        return f"{base}*"

    head, angle, rest = spelling.partition("<")
    head = imported_base(gen, head)
    if angle:
        spelling = head + angle + rest

    from siec.codegen.generics import split_generic

    if (parts := split_generic(spelling)) is not None:
        base, raw_args = parts
        base = imported_base(gen, base)
        interface = base in gen.interfaces
        args = [
            resolve_header_type(
                gen,
                arg,
                parameters,
                allow_interface=allow_interface,
                # A type argument names a type identity; whether it needs a
                # representation is decided by the template. Generic structs
                # validate their substituted fields, while aliases may erase
                # a phantom argument entirely.
                allow_opaque=True,
                allow_free=allow_free or interface,
            )
            for arg in raw_args
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

    base = imported_base(gen, spelling)
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

    for bound in constraints.values():
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
        resolve_constraints(gen, fn, parameters)
        resolve_constraints(
            gen,
            fn,
            parameters,
            attribute="receiver_constraints",
        )

        if fn.receiver is not None and not interface_action:
            receiver = fn.receiver
            if (fn.receiver_params is not None
                    and receiver not in fn.receiver_params
                    and "<" not in receiver
                    and not receiver.endswith("[]")):
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
