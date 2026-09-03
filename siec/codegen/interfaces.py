"""Interfaces: abstract types a struct nominally implements.

An interface declares fields and action signatures; 'struct S: I' claims
conformance, checked once every declaration is in. An interface-typed
parameter turns its function into a template: each call stamps an
instance for the concrete argument type, gated on it implementing the
interface. There is no runtime dispatch; everything monomorphizes.
"""

import re
from contextlib import contextmanager

from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator
from siec.codegen.generics import (split_generic, substitute,
                                   substitute_constraint, unify)
from siec.codegen.types import (
    INTEGER_TYPES,
    SCALAR_TYPES,
    SIGNED_TYPES,
    UNSIGNED_TYPES,
    is_const,
    strip_const,
    strip_reference,
)

IDENT = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")

# sealed markers the compiler owns for primitive categories; user code
# cannot claim them, and conformance is structural membership
SEALED_BUILTIN_INTERFACES = (
    "Scalar", "Numeric", "Integer", "SignedInteger", "UnsignedInteger",
)


@contextmanager
def declaration_view(gen: CodeGenerator, file: str):
    """Resolve a declaration-time check in the declaration's own file."""
    previous, gen.current_file = gen.current_file, file
    try:
        yield
    finally:
        gen.current_file = previous


def find_interface_spelling(gen: CodeGenerator, text: str | None,
                            shadowed=frozenset()):
    """
    The first complete interface spelling inside a type name: the bare
    name or, with its '<...>', the whole generic form. Returns the
    spelling with its start and end, or None. Lexical type parameters
    shadow same-named interfaces.
    """
    if not text:
        return None

    for match in IDENT.finditer(text):
        written = match.group()
        if "." in written:
            resolved = gen.resolve_qualified(written.split("."))
        else:
            if written in shadowed:
                continue
            resolved = gen.resolve_type_symbol(written)

        if resolved not in gen.interfaces:
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
    if (fn.receiver_params is not None
            and fn.receiver not in fn.receiver_params
            and "<" not in fn.receiver
            and not fn.receiver.endswith("[]")):
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
        shadowed = frozenset(
            [*(fn.receiver_params or ()), *(fn.type_params or ())]
        )

        # a method's '&self' receiver stands for its own type, never an
        # interface; a static method's first parameter adapts like any
        start = 1 if takes_self(fn) else 0
        for param in fn.params[start:]:
            while (found := find_interface_spelling(
                    gen, param.type, shadowed)) is not None:
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
            if find_interface_spelling(
                    gen, fn.return_type, shadowed) is not None:
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


def resolve_action_declaration(gen: CodeGenerator, fn) -> None:
    """
    Resolve an interface action: a bodiless method signature on an
    interface receiver, required of every implementing struct.
    """
    with source_location(line=fn.line, file=fn.file):
        if fn.is_override:
            raise TypeError("'@override' cannot decorate an interface action")

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
        start = 1 if takes_self(fn) else 0
        signature = (takes_self(fn), *(p.type for p in fn.params[start:]))
        overloads = gen.interface_actions.setdefault(key, [])
        if any((takes_self(other),
                *(p.type for p in other.params[
                    1 if takes_self(other) else 0:])) == signature
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
    from siec.codegen.headers import imported_base

    head, angle, rest = spelling.partition("<")
    spelling = imported_base(gen, head) + angle + rest
    if (parts := split_generic(spelling)) is None:
        return spelling

    base, args = parts
    return f"{base}<{','.join(expand_lax(gen, a) for a in args)}>"


def declare_implements(gen: CodeGenerator, name: str, template_base: str,
                       spellings: list[str], line: int, file: str) -> None:
    """
    Record what a struct claims to implement and queue it for resolution.

    Generic struct instances may add claims while function bodies are being
    checked. They join the same worklist as source declarations instead of
    initiating a conformance check from type resolution.
    """
    with declaration_view(gen, file):
        canonical = [canonical_interface(gen, s) for s in spellings]
    gen.implements.setdefault(name, set()).update(canonical)

    entry = (name, template_base, canonical, line, file)
    gen.pending_conformance.append(entry)


def resolve_conformance(gen: CodeGenerator) -> None:
    """
    Resolve every queued claim's method dependencies without checking it.

    Receiver-family lookup may stamp concrete method headers. Doing that here
    keeps specialization in resolution; the later conformance check only
    compares already-resolved declarations.
    """
    from siec.codegen.methods import resolve_method

    while gen.pending_conformance:
        entry = gen.pending_conformance.popleft()
        name, _, spellings, line, file = entry

        with source_location(line=line, file=file), declaration_view(gen, file):
            for spelling in spellings:
                base = (split_generic(spelling) or (spelling, []))[0]
                if base not in gen.interfaces:
                    continue

                for action_iface, method in gen.interface_actions:
                    if action_iface == base:
                        resolve_method(gen, name, method)

        gen.resolved_conformance.append(entry)


def run_conformance(gen: CodeGenerator) -> None:
    """Check resolved claims without initiating method specialization."""
    if gen.pending_conformance:
        raise RuntimeError("cannot check unresolved interface claims")

    while gen.resolved_conformance:
        before = (
            frozenset(gen.structs),
            frozenset(gen.instantiated_functions),
            frozenset(gen.return_types),
            len(gen.pending_functions),
            len(gen.pending_conformance),
        )
        check_conformance(gen, *gen.resolved_conformance.popleft())
        after = (
            frozenset(gen.structs),
            frozenset(gen.instantiated_functions),
            frozenset(gen.return_types),
            len(gen.pending_functions),
            len(gen.pending_conformance),
        )
        if after != before:
            raise RuntimeError(
                "conformance checking attempted semantic resolution")


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


def validate_interface_claim(gen: CodeGenerator, spelling: str,
                             receiver: str):
    """
    Validate the shared rules for one interface claim.

    The receiver-specific caller checks constraints, fields, and required
    methods after this returns the interface base, arguments, and declaration.
    """
    base, args = split_generic(spelling) or (spelling, [])
    iface = gen.interfaces.get(base)
    if iface is None:
        kind = ("a struct, not" if base in gen.structs
                or base in gen.generic_structs else "not")
        raise TypeError(f"{base!r} is {kind} an interface: "
                        f"{receiver!r} cannot implement it")

    if base in SEALED_BUILTIN_INTERFACES:
        raise TypeError(f"{base!r} is a sealed builtin interface: "
                        "only compiler-defined primitive types implement it")

    declared = len(iface.params or ())
    if declared != len(args):
        raise TypeError(f"interface {base!r} takes {declared} type "
                        f"argument{'s' if declared != 1 else ''}, "
                        f"got {len(args)}")

    return base, args, iface


def check_conformance(gen: CodeGenerator, name: str, template_base: str,
                      spellings: list[str], line: int, file: str) -> None:
    """
    Check one struct against every interface it claims: the fields
    declared, the actions provided with matching signatures.
    """
    with source_location(line=line, file=file), declaration_view(gen, file):
        info = gen.structs.get(name)
        fields = info.fields if info is not None else None

        for spelling in spellings:
            base, args, iface = validate_interface_claim(
                gen, spelling, name)

            mapping = dict(zip(iface.params or (), args))
            mapping["Self"] = name
            check_constraints(gen, iface, mapping)

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

    action_interface = (split_generic(spelling) or (spelling, []))[0]

    def bare(param: str) -> str:
        return strip_const(strip_reference(strip_const(param)))

    def value_shape(param: str) -> str:
        if action_interface in ("Assign", "AssignFrom"):
            return param
        return bare(param)

    required_instance = takes_self(action)
    required_const = required_instance and is_const(action.params[0].type)
    requires_mutable_receiver = action_interface in (
        "Assign", "AssignFrom", "Destroy")
    required_start = 1 if required_instance else 0
    required = [expand_lax(gen, substitute(p.type, mapping))
                for p in action.params[required_start:]]
    required_ret = action.return_type and expand_lax(
        gen, substitute(action.return_type, mapping))

    symbol = resolve_method(gen, name, method, specialize=False)
    if symbol is None:
        wanted = f"{method}({', '.join(required)})"
        if required_ret is not None:
            wanted += f" -> {required_ret}"
        raise TypeError(f"{noun(gen, template_base)} {template_base!r} does not implement "
                        f"{spelling!r}: it is missing the method '{wanted}'")

    shape_matched = False
    receiver_kind_mismatch = False
    receiver_const_mismatch = False
    receiver_kind_matched = False
    receiver_matched = False

    def receiver_matches(params: list[str]) -> bool:
        """
        Whether a candidate has the receiver kind and const capability
        promised by the action.
        """
        nonlocal receiver_kind_mismatch, receiver_const_mismatch
        nonlocal receiver_kind_matched, receiver_matched

        first = params[0] if params else None
        instance = first is not None and strip_const(first) == f"&{name}"
        if instance != required_instance:
            receiver_kind_mismatch = True
            return False

        receiver_kind_matched = True
        if required_const and not is_const(first):
            receiver_const_mismatch = True
            return False
        if requires_mutable_receiver and is_const(first):
            receiver_const_mismatch = True
            return False

        receiver_matched = True
        return True

    for candidate in [s for _, s in gen.overloads.get(symbol, ())] or [symbol]:
        have_params = gen.param_types.get(candidate)
        if have_params is None:
            continue

        if not receiver_matches(have_params):
            continue

        have_values = have_params[1 if required_instance else 0:]
        if ([value_shape(p) for p in have_values]
                != [value_shape(p) for p in required]):
            continue

        shape_matched = True
        if implements_or_equals(gen, gen.return_types.get(candidate),
                                required_ret):
            return

    # A generic overload is checked as the instance the action would call.
    # Interface-adapted placeholders respell to their constraints; ordinary
    # type parameters unify with the required parameter and return types.
    for template in [t for t in (gen.generic_functions.get(symbol),
                                 *gen.generic_overloads.get(symbol, ()))
                     if t is not None]:
        synthetic = {
            param: constraint
            for param, constraint in (template.constraints or {}).items()
            if param.startswith("__")
        }
        bindings = dict(synthetic)
        type_params = [p for p in template.type_params or ()
                       if p not in synthetic]
        initial = [expand_lax(gen, substitute(p.type, bindings))
                   for p in template.params]
        if not receiver_matches(initial):
            continue

        start = 1 if required_instance else 0
        patterns = [substitute(p.type, bindings)
                    for p in template.params[start:]]
        if len(patterns) != len(required):
            continue

        try:
            for pattern, concrete in zip(patterns, required):
                unify(pattern, concrete, type_params, bindings)
            pattern_ret = (substitute(template.return_type, bindings)
                           if template.return_type is not None else None)
            unify(pattern_ret, required_ret, type_params, bindings)
        except TypeError:
            continue

        if any(param not in bindings for param in type_params):
            continue

        declared = {
            param: constraint
            for param, constraint in (template.constraints or {}).items()
            if not param.startswith("__")
        }
        if declared:
            try:
                check_constraints(gen, template, bindings, declared)
            except TypeError:
                continue

        have = [expand_lax(gen, substitute(pattern, bindings))
                for pattern in patterns]
        if ([value_shape(p) for p in have]
                != [value_shape(p) for p in required]):
            continue

        shape_matched = True
        ret = pattern_ret and expand_lax(gen, pattern_ret)
        if implements_or_equals(gen, ret, required_ret):
            return

    if receiver_const_mismatch and not receiver_matched:
        raise TypeError(
            f"{noun(gen, template_base)} {template_base!r} does not implement "
            f"{spelling!r}: method {method!r} must take a "
            "'const &self' receiver")

    if receiver_kind_mismatch and not receiver_kind_matched:
        kind = "an instance" if required_instance else "a static"
        raise TypeError(
            f"{noun(gen, template_base)} {template_base!r} does not implement "
            f"{spelling!r}: method {method!r} must be {kind} method")

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


def expand_bound(gen: CodeGenerator, spelling: str,
                 seen: tuple = ()) -> tuple[str, bool]:
    """
    Resolve a bound in its declaration's view. The bool says whether its
    canonical target is an interface, which ordinary alias expansion
    deliberately rejects as an abstract value type.
    """
    from siec.codegen.aliases import expand_alias

    head, angle, rest = spelling.partition("<")
    if "." in head:
        resolved = gen.resolve_qualified(head.split("."))
        if resolved is None:
            raise TypeError(f"unknown type {spelling!r}")
        spelling = resolved + angle + rest
    elif ((bound := gen.member_bindings.get((gen.current_file, head)))
          is not None):
        spelling = bound + angle + rest

    parts = split_generic(spelling)
    base = parts[0] if parts is not None else spelling
    if base in gen.interfaces:
        return canonical_interface(gen, spelling), True

    # An alias may deliberately name an interface for use as a bound even
    # though that abstract target cannot be used as a stored value type.
    if parts is None and spelling in gen.aliases:
        if spelling in seen:
            cycle = " -> ".join([*seen, spelling])
            raise TypeError(f"type alias cycle: {cycle}")
        return expand_bound(gen, gen.aliases[spelling], (*seen, spelling))

    if parts is not None and base in gen.generic_aliases:
        alias = gen.generic_aliases[base]
        if len(parts[1]) != len(alias.params):
            take = len(alias.params)
            raise TypeError(f"generic type alias {base!r} takes {take} type "
                            f"argument{'s' if take != 1 else ''}, "
                            f"got {len(parts[1])}")
        target = substitute(alias.type, dict(zip(alias.params, parts[1])))
        return expand_bound(gen, target, (*seen, base))

    return expand_alias(gen, spelling), False


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
    out of the requirement - 'Iterable<char>' names 'char[]' - or once
    its bounds select the known elements for a non-generic claim.
    """
    from siec.codegen.generics import unify

    found = [name for name in gen.implements
             if type_implements(gen, name, required)]

    if required == "Scalar":
        found.extend(name for name in SCALAR_TYPES if name not in found)
    if required == "Integer":
        found.extend(name for name in INTEGER_TYPES if name not in found)
    if required == "SignedInteger":
        found.extend(name for name in SIGNED_TYPES if name not in found)
    if required == "UnsignedInteger":
        found.extend(name for name in UNSIGNED_TYPES if name not in found)
    if required == "Truthy":
        candidates = (*SCALAR_TYPES, *gen.enums, *gen.any_names.values())
        found.extend(name for name in candidates
                     if intrinsic_truthy(gen, name) and name not in found)

    for param, claim, constraints, file in gen.array_claims:
        bindings: dict = {}
        unify(claim, required, [param], bindings)
        if param in bindings:
            if (constraints_hold(gen, constraints, bindings, file)
                    and f"{bindings[param]}[]" not in found):
                found.append(f"{bindings[param]}[]")
            continue

        # In 'T[]: Formattable', T does not occur in the claimed interface,
        # so unification cannot discover it from 'Formattable'. Expand the
        # family over the concrete types already known to this compilation
        # and let its bound select the eligible elements. This set is
        # deliberately finite: recursive claims such as
        # 'T: Formattable => T[]: Formattable' otherwise describe infinitely
        # many nested array types.
        if not unify_spelling(gen, required, claim):
            continue

        # A wrapped nested array contributes each of its element layers.
        # Those are the concrete substitutions needed to derive exactly the
        # runtime array types this interface case may encounter, without
        # attempting the family's infinite theoretical closure.
        runtime_elements = []
        for name in gen.any_names.values():
            element = strip_const(name)
            while element.endswith("[]"):
                element = element[:-2]
                runtime_elements.append(element)

        # An exact override makes its concrete receiver part of the known
        # type universe as well. In particular, overriding 'char[]::format'
        # supplies the T = char[] layer from which the same family derives
        # 'char[][]: Formattable'.
        method_receivers = [
            name.partition("::")[0]
            for name in gen.overridden_method_signatures
            if "::" in name
        ]

        candidates = dict.fromkeys((
            *gen.implements,
            *SCALAR_TYPES,
            *gen.structs,
            *gen.enums,
            *runtime_elements,
            *method_receivers,
        ))
        for concrete in candidates:
            mapping = {param: concrete}
            array = f"{concrete}[]"
            if (array not in found
                    and constraints_hold(gen, constraints, mapping, file)):
                found.append(array)

    return found


def claimed_interfaces(gen: CodeGenerator, concrete: str,
                       required: str | None = None) -> set[str]:
    """
    The concrete interface spellings a type claims, including an array
    family's claims with its element substituted. When one required
    interface is being queried, discard families with a different base
    before canonicalizing their arguments; inspecting ``Destroy`` must not
    instantiate types mentioned only by an unrelated ``Add<X>`` claim.
    """
    concrete = strip_const(concrete)
    required_base = ((split_generic(required) or (required, []))[0]
                     if required is not None else None)

    def relevant(spelling: str) -> bool:
        base = (split_generic(spelling) or (spelling, []))[0]
        return required_base is None or base == required_base

    claims = {
        claim for claim in gen.implements.get(concrete, set())
        if relevant(claim)
    }

    if concrete in SCALAR_TYPES and relevant("Scalar"):
        claims.add("Scalar")
    if intrinsic_numeric(concrete) and relevant("Numeric"):
        claims.add("Numeric")
    if concrete in INTEGER_TYPES and relevant("Integer"):
        claims.add("Integer")
    if concrete in SIGNED_TYPES and relevant("SignedInteger"):
        claims.add("SignedInteger")
    if concrete in UNSIGNED_TYPES and relevant("UnsignedInteger"):
        claims.add("UnsignedInteger")
    if intrinsic_truthy(gen, concrete) and relevant("Truthy"):
        claims.add("Truthy")

    if concrete.endswith("[]"):
        element = concrete[:-2]
        claims.update(
            canonical_interface(gen, substitute(claim, {param: element}))
            for param, claim, constraints, file in gen.array_claims
            if relevant(claim) and constraints_hold(
                gen, constraints, {param: element}, file)
        )

    for param, spellings, constraints, file in gen.generic_claims:
        spellings = [claim for claim in spellings if relevant(claim)]
        if not spellings:
            continue
        mapping = {param: concrete}
        if constraints_hold(gen, constraints, mapping, file):
            claims.update(
                canonical_interface(gen, substitute(claim, mapping))
                for claim in spellings
            )

    return claims


def type_implements(gen: CodeGenerator, concrete: str, required: str) -> bool:
    """
    Whether a concrete type implements an interface: by its declared
    claim, or, for a 'T[]' array, the family's '@extend' claims with its
    element substituted in. A free placeholder in the requirement -
    'Iterable<T>' with no T bound - matches any claim that spells it.
    """
    # Sealed builtins are structural: answering them directly also keeps a
    # blanket claim guarded by the same marker from consulting itself
    # recursively.
    if required == "Scalar":
        return strip_const(concrete) in SCALAR_TYPES
    if required == "Numeric":
        return intrinsic_numeric(concrete)
    if required == "Integer":
        return strip_const(concrete) in INTEGER_TYPES
    if required == "SignedInteger":
        return strip_const(concrete) in SIGNED_TYPES
    if required == "UnsignedInteger":
        return strip_const(concrete) in UNSIGNED_TYPES
    if required == "Truthy" and intrinsic_truthy(gen, concrete):
        return True

    # Blanket claims may depend on other blanket claims. A cycle supplies
    # no evidence of conformance, so fail that path closed while allowing
    # the remaining concrete and blanket claims to be considered.
    query = (strip_const(concrete), required)
    if query in gen.interface_queries:
        return False

    gen.interface_queries.add(query)
    try:
        claims = claimed_interfaces(gen, concrete, required)
    finally:
        gen.interface_queries.remove(query)

    if required in claims:
        return True

    return any(unify_spelling(gen, required, claim) for claim in claims)


def intrinsic_truthy(gen: CodeGenerator, concrete: str | None) -> bool:
    """Whether the compiler supplies a built-in Truthy implementation."""
    name = strip_const(concrete) if concrete is not None else ""
    return (
        name in SCALAR_TYPES
        or name in gen.enums
        or name.endswith(("*", "[]"))
        or name.startswith("fn(")
    )


def intrinsic_numeric(concrete: str | None) -> bool:
    """Whether the compiler supplies native numeric operator methods."""
    name = strip_const(concrete) if concrete is not None else ""
    return (len(name) > 1 and name[0] in "iuf" and name[1:].isdigit()
            and name in SCALAR_TYPES)


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


def resolve_extension_methods(gen: CodeGenerator) -> None:
    """
    Give an unbounded array extension's owned methods the placeholder its
    receiver introduces. This makes

        '@extend T[]: I { fn m(...) { ... } }'

    equivalent to the separate 'fn T[]::m(...)' spelling. Concrete element
    names, such as 'char[]', keep concrete methods.
    """
    for ext in gen.extension_declarations:
        if ext.params is not None or not ext.actions:
            continue
        if not ext.name.endswith("[]"):
            continue

        elem = ext.name[:-2]
        if not elem.isidentifier() or is_type_name(gen, elem):
            continue

        for action in ext.actions:
            action.receiver_params = [elem]


def collect_extensions(gen: CodeGenerator, program) -> None:
    """
    Add raw extension declarations to the declaration inventory.

    Collection records syntax only. It does not canonicalize receivers,
    inspect interfaces, publish bound evidence, or validate required methods.
    """
    if gen.declaration_inventory_complete:
        raise RuntimeError(
            "extension collection continued after its inventory was frozen")

    for ext in program.extends:
        identity = id(ext)
        if identity in gen.collected_extensions:
            continue

        gen.collected_extensions.add(identity)
        gen.extension_declarations.append(ext)


def resolve_extensions(gen: CodeGenerator) -> None:
    """
    Resolve every collected extension and its canonical interface-claim facts.

    This runs before struct fields instantiate bounded generics, making the
    complete resolved claim inventory available to bound checks.
    """
    for ext in gen.extension_declarations:
        identity = id(ext)
        if identity in gen.resolved_extensions:
            continue

        gen.current_file = ext.file
        with source_location(line=ext.line, file=ext.file):
            resolve_extension(gen, ext)

        gen.resolved_extensions.add(identity)


def check_extensions(gen: CodeGenerator) -> None:
    """Check required receiver-family methods on resolved extensions."""
    unresolved = (gen.collected_extensions - gen.resolved_extensions)
    if unresolved:
        raise RuntimeError("cannot check unresolved extension claims")

    for ext in gen.extension_declarations:
        identity = id(ext)
        if identity in gen.checked_extensions:
            continue

        gen.current_file = ext.file
        with source_location(line=ext.line, file=ext.file):
            check_extension(gen, ext)

        gen.checked_extensions.add(identity)


def resolve_extension(gen: CodeGenerator, ext) -> None:
    """Validate one extension receiver and resolve any interface claims."""
    from siec.codegen.aliases import expand_alias

    if ext.params is not None and ext.name in ext.params:
        resolve_type_family_extend(gen, ext)
        return

    if ext.name.endswith("[]") and ext.name[:-2].isidentifier():
        # a real element type claims for that one array; a placeholder claims
        # for the family
        elem = ext.name[:-2]
        if is_type_name(gen, elem):
            canonical = strip_const(expand_alias(gen, elem))
            if ext.interfaces:
                declare_implements(gen, f"{canonical}[]", ext.name,
                                   ext.interfaces, ext.line, ext.file)
        else:
            resolve_array_extend(gen, ext)
        return

    # 'Base<E>' over bare placeholder names extends the template; spelled
    # over real types, one concrete instantiation.
    parts = split_generic(ext.name)
    declared_placeholders = False
    if parts is not None and ext.params is not None:
        declared_placeholders = all(arg in ext.params for arg in parts[1])
    if (parts is not None and parts[0] in gen.generic_structs
            and (declared_placeholders
                 or not any(is_type_name(gen, arg) for arg in parts[1]))):
        resolve_template_extend(gen, ext, *parts)
        return

    # A struct, enum, or primitive extends: whatever a method can name as its
    # receiver.
    canonical = strip_const(expand_alias(gen, ext.name))
    info = gen.structs.get(canonical)
    if info is not None and info.fields is None:
        raise TypeError(f"cannot extend {ext.name!r}: the struct has "
                        "no body to extend")

    if (info is None and canonical not in SCALAR_TYPES
            and canonical not in gen.enums):
        raise TypeError(f"cannot extend {ext.name!r}: it does not "
                        "name a struct, an enum, or a primitive")

    if ext.interfaces:
        declare_implements(gen, canonical, ext.name, ext.interfaces,
                           ext.line, ext.file)


def check_extension(gen: CodeGenerator, ext) -> None:
    """Check one resolved family extension against registered actions."""
    if ext.params is not None and ext.name in ext.params:
        check_type_family_extend(gen, ext)
        return

    if ext.name.endswith("[]") and ext.name[:-2].isidentifier():
        elem = ext.name[:-2]
        if not is_type_name(gen, elem):
            check_array_extend(gen, ext)


def resolve_type_family_extend(gen: CodeGenerator, ext) -> None:
    """
    Resolve a blanket claim over one bare receiver placeholder:
    '@extend<T: Scalar> T: Iface'. Claims and receiver methods both filter
    through the same bound set at each concrete use.
    """
    if ext.params != [ext.name]:
        raise TypeError(f"type-family extension receiver {ext.name!r} must "
                        "be its one declared type parameter")

    for spelling in ext.interfaces:
        _, _, iface = validate_interface_claim(gen, spelling, ext.name)

        if iface.fields:
            raise TypeError(f"{ext.name!r} cannot implement {spelling!r}: "
                            "a blanket receiver carries no interface fields")

    if ext.interfaces:
        gen.generic_claims.append(
            (ext.name, ext.interfaces, ext.constraints, ext.file))


def check_type_family_extend(gen: CodeGenerator, ext) -> None:
    """Check a resolved type-family claim has every required method."""
    for spelling in ext.interfaces:
        base, args = split_generic(spelling) or (spelling, [])
        iface = gen.interfaces[base]
        for (action_iface, method), actions in gen.interface_actions.items():
            if (action_iface == base
                    and method not in gen.generic_receiver_methods):
                action = actions[0]
                mapping = dict(zip(iface.params or (), args))
                params = ", ".join(
                    expand_lax(gen, substitute(p.type, mapping))
                    for p in action.params[1:])
                wanted = f"{method}({params})"
                if action.return_type is not None:
                    wanted += (" -> " + expand_lax(
                        gen, substitute(action.return_type, mapping)))

                raise TypeError(f"{ext.name!r} does not implement "
                                f"{spelling!r}: it is missing the method "
                                f"'{wanted}'")


def is_type_name(gen: CodeGenerator, name: str) -> bool:
    """
    Whether a bare spelling names a known type rather than a placeholder.
    """
    from siec.codegen.types import SCALAR_TYPES

    return (not name.isidentifier() or name in SCALAR_TYPES
            or name in gen.structs or name in gen.enums
            or name in gen.aliases or name in gen.alias_targets
            or name in gen.generic_structs)


def resolve_template_extend(gen: CodeGenerator, ext, base: str,
                            args: list) -> None:
    """
    Record a generic struct family's conditional interface claims.

    The extension's placeholders and bounds are renamed to the struct
    template's environment. Each existing and future instance publishes the
    claims only when its arguments satisfy those bounds.
    """
    template = gen.generic_structs[base]
    claim_key = base
    if len(args) != len(template.params):
        claim_key = f"{base}#{len(args)}"
        template = gen.generic_structs.get(claim_key) or template
    if len(args) != len(template.params):
        take = len(template.params)
        raise TypeError(f"generic struct {base!r} takes {take} type "
                        f"argument{'s' if take != 1 else ''}, got {len(args)}")

    if not ext.interfaces:
        return

    renaming = dict(zip(args, template.params))
    claims = [substitute(s, renaming) for s in ext.interfaces]
    constraints = {
        renaming.get(param, param): substitute_constraint(bound, renaming)
        for param, bound in (ext.constraints or {}).items()
    }
    entry = (claims, constraints, ext.file, ext.line)
    gen.generic_struct_claims.setdefault(claim_key, []).append(entry)

    for name in list(gen.structs):
        parts = split_generic(name)
        if parts is not None and parts[0] == base:
            mapping = dict(zip(template.params, parts[1]))
            if constraints_hold(gen, constraints, mapping, ext.file):
                declare_implements(gen, name, base,
                                   [substitute(c, mapping) for c in claims],
                                   ext.line, ext.file)


def resolve_array_extend(gen: CodeGenerator, ext) -> None:
    """
    Resolve an array-family claim and publish it as bound evidence.
    """
    elem = ext.name[:-2]
    if ext.params is not None:
        if ext.params != [elem]:
            raise TypeError(f"array extension receiver {ext.name!r} must "
                            "use its one declared type parameter")

    for spelling in ext.interfaces:
        _, _, iface = validate_interface_claim(gen, spelling, ext.name)

        if iface.fields:
            raise TypeError(f"{ext.name!r} cannot implement {spelling!r}: "
                            "an array carries no interface fields")

        gen.array_claims.append(
            (elem, spelling, ext.constraints, ext.file))


def check_array_extend(gen: CodeGenerator, ext) -> None:
    """Check a resolved array-family claim has every required method."""
    elem = ext.name[:-2]
    for spelling in ext.interfaces:
        base, args = split_generic(spelling) or (spelling, [])
        iface = gen.interfaces[base]
        mapping = dict(zip(iface.params or (), args))
        for (action_iface, method), actions in gen.interface_actions.items():
            if (action_iface == base
                    and ("[]", method) not in gen.generic_methods):
                action = actions[0]
                params = ", ".join(
                    expand_lax(gen, substitute(p.type, mapping))
                    for p in action.params[1:])
                wanted = f"{method}({params})"
                if action.return_type is not None:
                    wanted += (" -> " + expand_lax(
                        gen, substitute(action.return_type, mapping)))

                raise TypeError(f"{ext.name!r} does not implement "
                                f"{spelling!r}: it is missing the method "
                                f"'{wanted}' ('fn {elem}[]::{method}')")


def check_constraints(gen: CodeGenerator, template, mapping: dict,
                      constraints: dict | None = None) -> None:
    """
    Check a template's bounds against one instantiation. An interface
    bound accepts any implementing type; every concrete type-like bound
    accepts its canonical type exactly, aliases included.
    """
    constraints = template.constraints if constraints is None else constraints
    check_constraint_set(gen, constraints, mapping, template.file)


def check_constraint_set(gen: CodeGenerator, constraints: dict | None,
                         mapping: dict, file: str) -> None:
    """
    Check a standalone generic environment, such as a bounded extension
    family, against one concrete substitution.
    """
    from siec.codegen.aliases import expand_alias

    with declaration_view(gen, file):
        for placeholder, value in (constraints or {}).items():
            concrete = mapping.get(placeholder)
            if concrete is None:
                continue

            bounds = value if isinstance(value, tuple) else (value,)
            for spelling in bounds:
                required, is_interface_bound = expand_bound(
                    gen, substitute(spelling, mapping))
                if is_interface_bound:
                    if not type_implements(gen, concrete, required):
                        raise TypeError(f"type {concrete!r} does not implement "
                                        f"interface {required!r}")
                    continue

                concrete = expand_alias(gen, concrete, checked=False)
                if strip_const(concrete) != strip_const(required):
                    raise TypeError(f"type {concrete!r} does not satisfy bound "
                                    f"{required!r}")


def constraints_hold(gen: CodeGenerator, constraints: dict | None,
                     mapping: dict, file: str) -> bool:
    """Whether one substitution satisfies a standalone bound set."""
    try:
        check_constraint_set(gen, constraints, mapping, file)
    except TypeError:
        return False

    return True
