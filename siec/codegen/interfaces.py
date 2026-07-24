"""Interfaces: abstract types a struct nominally implements.

An interface declares fields and action signatures; 'struct S: I' claims
conformance, checked once every declaration is in. An interface-typed
parameter turns its function into a template: each call stamps an
instance for the concrete argument type, gated on it implementing the
interface. There is no runtime dispatch; everything monomorphizes.
"""

import re

from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator
from siec.codegen.generics import split_generic, substitute
from siec.codegen.types import strip_const, strip_reference

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def find_interface_spelling(gen: CodeGenerator, text: str | None):
    """
    The first complete interface spelling inside a type name: the bare
    name or, with its '<...>', the whole generic form. Returns the
    spelling with its start and end, or None.
    """
    if not text:
        return None

    for match in IDENT.finditer(text):
        if match.group() not in gen.interfaces:
            continue

        end = match.end()
        if end < len(text) and text[end] == "<":
            depth = 0
            for i in range(end, len(text)):
                if text[i] == "<":
                    depth += 1
                elif text[i] == ">":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break

        return text[match.start():end], match.start(), end

    return None


def adapt_interface_params(gen: CodeGenerator, fn) -> None:
    """
    Rewrite a function's interface-typed parameters into type parameters
    constrained to the interface: 'fn f(n: Named)' becomes a template
    stamped per concrete argument type, each call checked to implement.

    Each parameter gets its own placeholder, so two 'Named' parameters
    take two independent implementing types.
    """
    previous, gen.current_file = gen.current_file, fn.file
    try:
        constraints = {}
        start = 1 if fn.receiver is not None and fn.params else 0
        for param in fn.params[start:]:
            while (found := find_interface_spelling(gen, param.type)) is not None:
                spelling, begin, end = found
                placeholder = f"__I{len(constraints)}"
                param.type = param.type[:begin] + placeholder + param.type[end:]
                constraints[placeholder] = spelling

        with source_location(line=fn.line, file=fn.file):
            if find_interface_spelling(gen, fn.return_type) is not None:
                raise TypeError(f"function {fn.name!r} cannot return an "
                                "interface value: return the concrete "
                                "struct type")

        if constraints:
            with source_location(line=fn.line, file=fn.file):
                if fn.is_extern:
                    raise TypeError(f"an '@extern' function cannot take an "
                                    "interface parameter: it names one "
                                    "foreign symbol")

            fn.type_params = [*(fn.type_params or []), *constraints]
            fn.constraints = {**(fn.constraints or {}), **constraints}
    finally:
        gen.current_file = previous


def register_action(gen: CodeGenerator, fn) -> None:
    """
    Register an interface action: a bodiless method signature on an
    interface receiver, required of every implementing struct.
    """
    with source_location(line=fn.line, file=fn.file):
        if fn.body is not None or fn.asm is not None:
            raise TypeError(f"an interface action cannot have a body: "
                            f"{fn.name!r} declares a required signature")

        iface = gen.interfaces[fn.receiver]
        declared = len(iface.params or ())
        given = len(fn.receiver_params or ())
        if declared != given:
            raise TypeError(f"interface {fn.receiver!r} takes {declared} type "
                            f"parameter{'s' if declared != 1 else ''}, "
                            f"its action spells {given}")

        # a name may overload, each signature its own requirement;
        # respelling one is the error it always was
        key = (fn.receiver, fn.name.partition("::")[2])
        signature = [p.type for p in fn.params[1:]]
        overloads = gen.interface_actions.setdefault(key, [])
        if any([p.type for p in other.params[1:]] == signature
               for other in overloads):
            raise TypeError(f"action {fn.name!r} is declared more than once")

        overloads.append(fn)


def canonical_interface(gen: CodeGenerator, spelling: str) -> str:
    """
    An interface spelling under canonical type arguments, so every way of
    writing one instance compares equal.
    """
    from siec.codegen.aliases import expand_alias

    if (parts := split_generic(spelling)) is None:
        return spelling

    base, args = parts
    return f"{base}<{','.join(expand_alias(gen, a, checked=False) for a in args)}>"


def declare_implements(gen: CodeGenerator, name: str, template_base: str,
                       spellings: list[str], line: int, file: str) -> None:
    """
    Record what a struct claims to implement and queue the conformance
    check, run once every method is declared; checks queued after that
    point run immediately.
    """
    canonical = [canonical_interface(gen, s) for s in spellings]
    gen.implements.setdefault(name, set()).update(canonical)

    entry = (name, template_base, canonical, line, file)
    if gen.conformance_ready:
        check_conformance(gen, *entry)
    else:
        gen.pending_conformance.append(entry)


def run_conformance(gen: CodeGenerator) -> None:
    """
    Drain the queued conformance checks; later claims check on the spot.
    """
    gen.conformance_ready = True
    while gen.pending_conformance:
        check_conformance(gen, *gen.pending_conformance.pop(0))


def noun(name: str) -> str:
    """
    What a conforming type calls itself in an error: arrays aren't structs.
    """
    return "type" if name.endswith("[]") else "struct"


def check_conformance(gen: CodeGenerator, name: str, template_base: str,
                      spellings: list[str], line: int, file: str) -> None:
    """
    Check one struct against every interface it claims: the fields
    declared, the actions provided with matching signatures.
    """
    with source_location(line=line, file=file):
        info = gen.structs.get(name)
        fields = info.fields if info is not None else None

        for spelling in spellings:
            base, args = split_generic(spelling) or (spelling, [])
            iface = gen.interfaces.get(base)
            if iface is None:
                kind = ("a struct, not" if base in gen.structs
                        or base in gen.generic_structs else "not")
                raise TypeError(f"{base!r} is {kind} an interface: "
                                f"{name!r} cannot implement it")

            declared = len(iface.params or ())
            if declared != len(args):
                raise TypeError(f"interface {base!r} takes {declared} type "
                                f"argument{'s' if declared != 1 else ''}, "
                                f"got {len(args)}")

            mapping = dict(zip(iface.params or (), args))

            # every interface field, at its declared type
            for required in iface.fields or ():
                required_type = expand_lax(gen, substitute(required.type, mapping))
                field = next((f for f in fields or ()
                              if f.name == required.name), None)
                if field is None:
                    raise TypeError(f"{noun(template_base)} {template_base!r} does not "
                                    f"implement {spelling!r}: it is missing "
                                    f"the field '{required.name}: "
                                    f"{required.type}'")

                if strip_const(expand_lax(gen, field.type)) != strip_const(required_type):
                    raise TypeError(f"{noun(template_base)} {template_base!r} does not "
                                    f"implement {spelling!r}: field "
                                    f"{required.name!r} must be "
                                    f"{required_type!r}, not {field.type!r}")

            # every action, resolvable as a method with the right signature
            for (action_iface, method), actions in list(gen.interface_actions.items()):
                if action_iface != base:
                    continue

                for action in actions:
                    check_action(gen, name, template_base, spelling, method,
                                 action, mapping)


def check_action(gen: CodeGenerator, name: str, template_base: str,
                 spelling: str, method: str, action, mapping: dict) -> None:
    """
    Check one required action against the struct's methods of that name:
    any overload matching the substituted signature satisfies it. A '&T'
    or 'const &T' parameter satisfies a required T - the reference only
    marks how the same value passes.
    """
    from siec.codegen.methods import resolve_method

    symbol = resolve_method(gen, name, method)
    if symbol is None:
        raise TypeError(f"{noun(template_base)} {template_base!r} does not implement "
                        f"{spelling!r}: it is missing the method {method!r}")

    def bare(param: str) -> str:
        return strip_const(strip_reference(strip_const(param)))

    required = [expand_lax(gen, substitute(p.type, mapping))
                for p in action.params[1:]]
    required_ret = action.return_type and expand_lax(
        gen, substitute(action.return_type, mapping))

    shape_matched = False
    for candidate in [s for _, s in gen.overloads.get(symbol, ())] or [symbol]:
        # a still-generic method matches by existence
        have_params = gen.param_types.get(candidate)
        if have_params is None:
            return

        if [bare(p) for p in have_params[1:]] != [bare(p) for p in required]:
            continue

        shape_matched = True
        if implements_or_equals(gen, gen.return_types.get(candidate),
                                required_ret):
            return

    if shape_matched:
        raise TypeError(f"{noun(template_base)} {template_base!r} does not implement "
                        f"{spelling!r}: method {method!r} must return "
                        f"{required_ret!r}")

    raise TypeError(f"{noun(template_base)} {template_base!r} does not implement "
                    f"{spelling!r}: method {method!r} must take "
                    f"({', '.join(required)})")


def implements_or_equals(gen: CodeGenerator, have: str | None,
                         required: str | None) -> bool:
    """
    Whether a provided return type satisfies a required one: the same
    type, or, when the requirement is an interface, any implementer.
    """
    if have == required:
        return True

    if required is None or have is None:
        return False

    base = required.partition("<")[0]
    if base in gen.interfaces:
        return type_implements(gen, have, required)

    return strip_const(have) == strip_const(required)


def expand_lax(gen: CodeGenerator, name: str | None) -> str | None:
    """
    Expand a type spelling without visibility gating, for signature
    comparison; interface names pass through as themselves.
    """
    from siec.codegen.aliases import expand_alias

    if name is None:
        return None

    base = name.partition("<")[0].removeprefix("const ").lstrip("&")
    if base in gen.interfaces:
        return canonical_interface(gen, name)

    return expand_alias(gen, name, checked=False)


def type_implements(gen: CodeGenerator, concrete: str, required: str) -> bool:
    """
    Whether a concrete type implements an interface: by its declared
    claim, or, for a 'T[]' array, the builtin 'Iterable<T>' and the
    family's '@extend' claims with its element substituted in.
    """
    concrete = strip_const(concrete)
    if required in gen.implements.get(concrete, set()):
        return True

    if not concrete.endswith("[]"):
        return False

    elem = concrete[:-2]
    if required == f"Iterable<{elem}>":
        return True

    return any(canonical_interface(gen, substitute(s, {param: elem})) == required
               for param, s in gen.array_claims)


def register_extends(gen: CodeGenerator, program) -> None:
    """
    Register every '@extend Type: Iface, ...;' claim before conformance
    runs: a struct's (through an alias too) queues the checks its own
    declaration would; a generic template's carries to every
    instantiation, the already-stamped ones caught up on the spot; one
    array's ('char[]') claims for exactly that element, checked like a
    struct's; the family's ('T[]', its element a placeholder) checks
    that every action has its 'T[]::m' template and answers queries per
    element.
    """
    from siec.codegen.aliases import expand_alias

    for ext in program.extends:
        gen.current_file = ext.file
        with source_location(line=ext.line, file=ext.file):
            if ext.name.endswith("[]") and ext.name[:-2].isidentifier():
                # a real element type claims for that one array; a
                # placeholder claims for the family
                elem = ext.name[:-2]
                if is_type_name(gen, elem):
                    canonical = strip_const(expand_alias(gen, elem))
                    declare_implements(gen, f"{canonical}[]", ext.name,
                                       ext.interfaces, ext.line, ext.file)
                else:
                    register_array_extend(gen, ext)
                continue

            # 'Base<E>' over bare placeholder names extends the template;
            # spelled over real types, one concrete instantiation
            parts = split_generic(ext.name)
            if (parts is not None and parts[0] in gen.generic_structs
                    and not any(is_type_name(gen, arg) for arg in parts[1])):
                register_template_extend(gen, ext, *parts)
                continue

            canonical = strip_const(expand_alias(gen, ext.name))
            info = gen.structs.get(canonical)
            if info is None or info.fields is None:
                raise TypeError(f"cannot extend {ext.name!r}: it does not "
                                "name a struct")

            declare_implements(gen, canonical, ext.name, ext.interfaces,
                               ext.line, ext.file)


def is_type_name(gen: CodeGenerator, name: str) -> bool:
    """
    Whether a bare spelling names a known type rather than a placeholder.
    """
    from siec.codegen.types import SCALAR_TYPES

    return (not name.isidentifier() or name in SCALAR_TYPES
            or name in gen.structs or name in gen.enums
            or name in gen.aliases or name in gen.generic_structs)


def register_template_extend(gen: CodeGenerator, ext, base: str,
                             args: list) -> None:
    """
    Add claims to a generic struct template, the written placeholders
    renamed to the template's own; instances stamped before this
    declaration catch up on the spot.
    """
    template = gen.generic_structs[base]
    if len(args) != len(template.params):
        take = len(template.params)
        raise TypeError(f"generic struct {base!r} takes {take} type "
                        f"argument{'s' if take != 1 else ''}, got {len(args)}")

    renaming = dict(zip(args, template.params))
    claims = [substitute(s, renaming) for s in ext.interfaces]
    template.interfaces = [*(template.interfaces or ()), *claims]

    for name in list(gen.structs):
        parts = split_generic(name)
        if parts is not None and parts[0] == base:
            mapping = dict(zip(template.params, parts[1]))
            declare_implements(gen, name, base,
                               [substitute(c, mapping) for c in claims],
                               ext.line, ext.file)


def register_array_extend(gen: CodeGenerator, ext) -> None:
    """
    Record the arrays' claims, the element name a placeholder: each
    action must have its 'T[]::m' template declared, and the stamped
    signatures check themselves per element at each use.
    """
    elem = ext.name[:-2]
    for spelling in ext.interfaces:
        base, args = split_generic(spelling) or (spelling, [])
        iface = gen.interfaces.get(base)
        if iface is None:
            kind = ("a struct, not" if base in gen.structs
                    or base in gen.generic_structs else "not")
            raise TypeError(f"{base!r} is {kind} an interface: "
                            f"{ext.name!r} cannot implement it")

        declared = len(iface.params or ())
        if declared != len(args):
            raise TypeError(f"interface {base!r} takes {declared} type "
                            f"argument{'s' if declared != 1 else ''}, "
                            f"got {len(args)}")

        if iface.fields:
            raise TypeError(f"{ext.name!r} cannot implement {spelling!r}: "
                            "an array carries no interface fields")

        for (action_iface, method) in gen.interface_actions:
            if (action_iface == base
                    and ("[]", method) not in gen.generic_methods):
                raise TypeError(f"{ext.name!r} does not implement "
                                f"{spelling!r}: it is missing the method "
                                f"{method!r} ('fn {elem}[]::{method}')")

        gen.array_claims.append((elem, spelling))


def check_constraints(gen: CodeGenerator, template, mapping: dict) -> None:
    """
    Check a template's interface constraints against one instantiation:
    each bound type must implement the constraining interface.
    """
    for placeholder, spelling in (template.constraints or {}).items():
        concrete = mapping.get(placeholder)
        if concrete is None:
            continue

        required = canonical_interface(gen, substitute(spelling, mapping))
        if not type_implements(gen, concrete, required):
            raise TypeError(f"type {concrete!r} does not implement "
                            f"interface {required!r}")
