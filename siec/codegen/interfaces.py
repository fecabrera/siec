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
from siec.codegen.types import SCALAR_TYPES, strip_const, strip_reference

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


def takes_self(fn) -> bool:
    """
    Whether a method's first parameter is its receiver, the shape that
    makes it an instance method. Any other first parameter is a static
    method's own, and adapts like the rest.
    """
    if fn.receiver is None or not fn.params:
        return False

    # an array receiver already spells its element in the reference
    spelling = fn.receiver
    if fn.receiver_params is not None and not fn.receiver.endswith("[]"):
        spelling += f"<{','.join(fn.receiver_params)}>"

    return strip_const(fn.params[0].type) == f"&{spelling}"


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

        # a method's '&self' receiver stands for its own type, never an
        # interface; a static method's first parameter adapts like any
        start = 1 if takes_self(fn) else 0
        for param in fn.params[start:]:
            while (found := find_interface_spelling(gen, param.type)) is not None:
                spelling, begin, end = found

                # 'Iterable<T>[]' constrains the whole array argument:
                # the brackets fold into the placeholder, so the array
                # type binds and checks against the interface itself
                if param.type[end:end + 2] == "[]":
                    end += 2

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
    writing one instance compares equal. An argument may itself be an
    interface - it only ever lands in an action's parameter, where an
    interface is allowed - so arguments expand laxly.
    """
    if (parts := split_generic(spelling)) is None:
        return spelling

    base, args = parts
    return f"{base}<{','.join(expand_lax(gen, a) for a in args)}>"


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


def noun(gen: CodeGenerator, name: str) -> str:
    """
    What a conforming type calls itself in an error: only a struct is a
    struct - an array, an enum, and a primitive are plain types, and an
    alias is whatever it names.
    """
    from siec.codegen.aliases import expand_alias

    canonical = strip_const(expand_alias(gen, name, checked=False)) or name
    base = split_generic(canonical)
    base = base[0] if base is not None else canonical
    return ("struct" if base in gen.structs or base in gen.generic_structs
            else "type")


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
                    raise TypeError(f"{noun(gen, template_base)} {template_base!r} does not "
                                    f"implement {spelling!r}: it is missing "
                                    f"the field '{required.name}: "
                                    f"{required.type}'")

                if strip_const(expand_lax(gen, field.type)) != strip_const(required_type):
                    raise TypeError(f"{noun(gen, template_base)} {template_base!r} does not "
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

    def bare(param: str) -> str:
        return strip_const(strip_reference(strip_const(param)))

    required = [expand_lax(gen, substitute(p.type, mapping))
                for p in action.params[1:]]
    required_ret = action.return_type and expand_lax(
        gen, substitute(action.return_type, mapping))

    symbol = resolve_method(gen, name, method)
    if symbol is None:
        wanted = f"{method}({', '.join(required)})"
        if required_ret is not None:
            wanted += f" -> {required_ret}"
        raise TypeError(f"{noun(gen, template_base)} {template_base!r} does not implement "
                        f"{spelling!r}: it is missing the method '{wanted}'")

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

    # an interface-taking overload lives on as a template, its parameters
    # respelled as constrained placeholders; the constraints substitute
    # back to compare its true shape
    for template in [t for t in (gen.generic_functions.get(symbol),
                                 *gen.generic_overloads.get(symbol, ()))
                     if t is not None]:
        constraints = template.constraints or {}
        if any(p not in constraints for p in template.type_params or ()):
            return  # a still-generic method matches by existence

        have = [expand_lax(gen, substitute(p.type, constraints))
                for p in template.params[1:]]
        if [bare(p) for p in have] != [bare(p) for p in required]:
            continue

        shape_matched = True
        ret = template.return_type and expand_lax(
            gen, substitute(template.return_type, constraints))
        if implements_or_equals(gen, ret, required_ret):
            return

    if shape_matched:
        raise TypeError(f"{noun(gen, template_base)} {template_base!r} does not implement "
                        f"{spelling!r}: method {method!r} must return "
                        f"{required_ret!r}")

    raise TypeError(f"{noun(gen, template_base)} {template_base!r} does not implement "
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


def interface_expansions(gen: CodeGenerator, spelling: str) -> list[str]:
    """
    Concrete types satisfying an interface spelling whose arguments may
    themselves be interfaces: each inner interface substitutes each of
    its implementers, the outer expanding per combination, so
    'Iterable<Formattable>' names every iterable of every formattable.
    """
    parts = split_generic(spelling)
    if parts is not None:
        base, args = parts
        for index, arg in enumerate(args):
            inner = (split_generic(arg) or (arg, None))[0]
            if inner in gen.interfaces:
                found = []
                for concrete in interface_expansions(gen, arg):
                    respelled = [*args[:index], concrete, *args[index + 1:]]
                    found.extend(interface_expansions(
                        gen, f"{base}<{','.join(respelled)}>"))

                return list(dict.fromkeys(found))

    return interface_implementers(gen, canonical_interface(gen, spelling))


def interface_implementers(gen: CodeGenerator, required: str) -> list[str]:
    """
    Every type known to implement an interface: the declared claims,
    plus the array the family's claim spells once its element unifies
    out of the requirement - 'Iterable<char>' names 'char[]'.
    """
    from siec.codegen.generics import unify

    found = [name for name in gen.implements
             if type_implements(gen, name, required)]

    for param, claim in gen.array_claims:
        bindings: dict = {}
        unify(claim, required, [param], bindings)
        if param in bindings and f"{bindings[param]}[]" not in found:
            found.append(f"{bindings[param]}[]")

    return found


def type_implements(gen: CodeGenerator, concrete: str, required: str) -> bool:
    """
    Whether a concrete type implements an interface: by its declared
    claim, or, for a 'T[]' array, the family's '@extend' claims with its
    element substituted in. A free placeholder in the requirement -
    'Iterable<T>' with no T bound - matches any claim that spells it.
    """
    concrete = strip_const(concrete)
    claims = set(gen.implements.get(concrete, set()))

    if concrete.endswith("[]"):
        elem = concrete[:-2]
        claims.update(canonical_interface(gen, substitute(s, {param: elem}))
                      for param, s in gen.array_claims)

    if required in claims:
        return True

    return any(unify_spelling(gen, required, claim) for claim in claims)


def unify_spelling(gen: CodeGenerator, required: str, provided: str) -> bool:
    """
    Whether a required interface spelling matches a provided claim, its
    free placeholder names binding to whatever the claim spells there:
    'Iterable<T>' takes a claimed 'Iterable<char>' with T as char. A
    known type or interface name is never free - it matches only itself.
    """
    if required == provided:
        return True

    req, have = split_generic(required), split_generic(provided)
    if req is None or have is None:
        # a bare interface spelling takes any instantiation of itself
        if (required in gen.interfaces and have is not None
                and have[0] == required):
            return True

        # a bare free name takes the whole provided spelling
        return required is not None and free_name(gen, required)

    if req[0] != have[0] or len(req[1]) != len(have[1]):
        return False

    bindings = {}
    for arg, spelled in zip(req[1], have[1]):
        arg = substitute(arg, bindings)
        if arg == spelled:
            continue

        if split_generic(arg) is not None:
            if not unify_spelling(gen, arg, spelled):
                return False
            continue

        if not free_name(gen, arg):
            return False

        bindings[arg] = spelled

    return True


def free_name(gen: CodeGenerator, spelling: str) -> bool:
    """
    Whether a spelling is a free placeholder name: a bare identifier
    naming no known type and no interface.
    """
    return (spelling.isidentifier() and not is_type_name(gen, spelling)
            and spelling not in gen.interfaces)


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

            # a struct, an enum, or a primitive extends: whatever a
            # method can name as its receiver
            canonical = strip_const(expand_alias(gen, ext.name))
            info = gen.structs.get(canonical)
            if info is not None and info.fields is None:
                raise TypeError(f"cannot extend {ext.name!r}: the struct has "
                                "no body to extend")

            if (info is None and canonical not in SCALAR_TYPES
                    and canonical not in gen.enums):
                raise TypeError(f"cannot extend {ext.name!r}: it does not "
                                "name a struct, an enum, or a primitive")

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

        mapping = dict(zip(iface.params or (), args))
        for (action_iface, method), actions in gen.interface_actions.items():
            if (action_iface == base
                    and ("[]", method) not in gen.generic_methods):
                action = actions[0]
                params = ", ".join(expand_lax(gen, substitute(p.type, mapping))
                                   for p in action.params[1:])
                wanted = f"{method}({params})"
                if action.return_type is not None:
                    wanted += (" -> " + expand_lax(
                        gen, substitute(action.return_type, mapping)))

                raise TypeError(f"{ext.name!r} does not implement "
                                f"{spelling!r}: it is missing the method "
                                f"'{wanted}' ('fn {elem}[]::{method}')")

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
