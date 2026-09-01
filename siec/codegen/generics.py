"""Monomorphization of generics.

A 'struct S<T>' or 'fn f<T>' declaration registers a template; each
concrete spelling - 'S<i32>' in a type position, 'f(x)' or 'f<i32>(x)'
at a call - stamps out a real struct or function under its canonical
name, so every use of the same arguments shares one instantiation.
"""

import copy
import re
from dataclasses import fields as dataclass_fields, is_dataclass

from siec.ast import (AggregateLiteral, ArrayLiteral, Call, Field, SizeOf,
                      TypeId, TypeName)
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator, StructInfo
from siec.codegen.types import (
    fn_type_parts,
    is_reference,
    raw_array,
    strip_const,
    strip_reference,
    validate_type,
)

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
INSTANTIATION = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)<")


def constraint_bounds(value: str | tuple) -> tuple[str, ...]:
    """Return one bound value as its normalized intersection members."""
    return value if isinstance(value, tuple) else (value,)


def constraint_count(constraints: dict | None) -> int:
    """Count individual bounds, including intersections on one parameter."""
    return sum(len(constraint_bounds(value))
               for value in (constraints or {}).values())


def substitute_constraint(value: str | tuple, mapping: dict):
    """Substitute every type spelling in one possibly intersected bound."""
    bounds = tuple(substitute(bound, mapping)
                   for bound in constraint_bounds(value))
    return bounds[0] if len(bounds) == 1 else bounds


def check_template_cycle(gen: CodeGenerator, name: str) -> None:
    """
    Reject a generic alias whose target reaches back to itself through
    other generic aliases: 'A<T> = B<T>; B<T> = A<T>' can only loop.

    Plain aliases expand eagerly at declaration, so only the edges among
    templates need walking here; mixed cycles surface through that
    eager expansion.
    """
    def references(base: str):
        target = gen.generic_aliases[base].type
        return [m.group(1) for m in INSTANTIATION.finditer(target)
                if m.group(1) in gen.generic_aliases]

    def visit(base: str, path: tuple) -> None:
        if base in path:
            cycle = [*path[path.index(base):], base]
            raise TypeError("type alias cycle: " + " -> ".join(cycle))

        for ref in references(base):
            visit(ref, (*path, base))

    visit(name, ())


def split_generic(name: str) -> tuple[str, list[str]] | None:
    """
    Split a generic spelling 'Name<A,B>' into its base name and argument
    names, or None for any other shape.

    Arguments split on top-level commas only: brackets of any kind nest,
    and the '>' of a function type's '->' closes nothing.
    """
    base, sep, rest = name.partition("<")
    if not sep or not base.isidentifier() or not name.endswith(">"):
        return None

    inner = rest[:-1]
    args, depth, start = [], 0, 0
    for i, char in enumerate(inner):
        if char in "<([{":
            depth += 1
        elif char == ">" and inner[i - 1:i] == "-":
            continue
        elif char in ">)]}":
            depth -= 1
        elif char == "," and depth == 0:
            args.append(inner[start:i])
            start = i + 1

    if depth != 0:
        return None

    return base, [*args, inner[start:]]


def substitute(type_name: str, mapping: dict) -> str:
    """
    Replace each type parameter's whole-identifier occurrences in a field's
    type name: 'T*' becomes 'i32*', 'Tx' stays itself.
    """
    return IDENT.sub(lambda m: mapping.get(m.group(), m.group()), type_name)


def instantiate_generic(gen: CodeGenerator, name: str, seen: tuple = (),
                        checked: bool = True) -> str | None:
    """Instantiate a generic type while the semantic graph is still open."""
    gen.generic_type_depth += 1
    try:
        return _instantiate_generic(gen, name, seen, checked)
    finally:
        gen.generic_type_depth -= 1


def _instantiate_generic(gen: CodeGenerator, name: str, seen: tuple = (),
                         checked: bool = True) -> str | None:
    """
    Instantiate a generic spelling into a concrete canonical name: a
    struct template registers a real struct, an alias template expands
    its substituted target; None when the base is not a known template.

    A struct's identified type registers before its fields resolve, so a
    field may point at the instantiation itself, or at a mutually
    generic one.
    """
    # deferred imports: instantiation is a stage of alias expansion
    from siec.codegen.aliases import expand_alias
    if (parts := split_generic(name)) is None:
        return None

    base, args = parts

    # 'Tuple<A, B, ...>' is builtin and variadic: each arity synthesizes
    # its own struct, elements indexed 't[0]', 't[1]', ...
    if base == "Tuple":
        args = [expand_alias(gen, arg, seen) for arg in args]
        if not args or not all(args):
            raise TypeError("a Tuple needs its element types: 'Tuple<A, B, ...>'")

        for arg in args:
            if arg.startswith("const ") or arg.startswith("&"):
                raise TypeError(f"cannot instantiate 'Tuple' with {arg!r}: "
                                "the argument carries a modifier")

        canonical = f"Tuple<{','.join(args)}>"
        if canonical not in gen.structs:
            if gen.types_lowered:
                raise RuntimeError(
                    "LLVM emission attempted to instantiate a tuple type")
            fields = [Field(str(i), arg) for i, arg in enumerate(args)]
            gen.structs[canonical] = StructInfo(None, fields)

            gen.ungated_types += 1
            try:
                for field in fields:
                    validate_type(field.type, gen.structs)
            finally:
                gen.ungated_types -= 1

        return canonical

    alias = gen.generic_aliases.get(base)
    template = gen.generic_structs.get(base)

    # the argument count picks among same-named struct templates:
    # 'Result<E>' and 'Result<V, E>' are distinct shapes
    if template is not None and len(args) != len(template.params):
        template = gen.generic_structs.get(f"{base}#{len(args)}") or template

    if alias is None and template is None:
        return None

    # a written template name must be visible to the using file
    if checked and not gen.sees(base):
        raise TypeError(f"unknown type {base!r}")

    params = alias.params if alias is not None else template.params
    kind = "type alias" if alias is not None else "struct"

    # an unchecked expansion stays unchecked into its arguments: the
    # caller vouched for the spelling as a whole
    args = [expand_alias(gen, arg, seen, checked) for arg in args]
    if len(args) != len(params):
        take = len(params)
        raise TypeError(f"generic {kind} {base!r} takes {take} type "
                        f"argument{'s' if take != 1 else ''}, got {len(args)}")

    # a modifier marks a whole written type; substituted into a derived
    # position ('T*'), it would silently move where it applies
    for arg in args:
        if arg.startswith("const ") or arg.startswith("&"):
            raise TypeError(f"cannot instantiate {base!r} with {arg!r}: "
                            "the argument carries a modifier")

    constraint_owner = alias if alias is not None else template
    if constraint_owner.constraints:
        from siec.codegen.interfaces import check_constraints

        check_constraints(gen, constraint_owner, dict(zip(params, args)))

    # a generic alias expands its target with the arguments substituted,
    # like any alias one step further; 'seen' catches self-reference; the
    # substituted target mixes files' names, so no view gates it
    if alias is not None:
        if base in seen:
            cycle = " -> ".join([*seen, base])
            raise TypeError(f"type alias cycle: {cycle}")

        target = substitute(alias.type, dict(zip(params, args)))
        gen.ungated_types += 1
        try:
            return expand_alias(gen, target, (*seen, base))
        finally:
            gen.ungated_types -= 1

    canonical = f"{base}<{','.join(args)}>"
    if canonical in gen.structs:
        return canonical
    if gen.types_lowered:
        raise RuntimeError(
            "LLVM emission attempted to instantiate a generic type")

    if gen.current_function is not None:
        gen.type_instantiation_sites.setdefault(
            canonical,
            (gen.current_function, gen.current_file, gen.current_line),
        )

    if template.fields is None:
        raise TypeError(f"generic struct {base!r} is declared without a body")

    mapping = dict(zip(template.params, args))

    # fields deep-copy so each instantiation owns its types and defaults
    fields = copy.deepcopy(template.fields)
    substitute_types(fields, mapping)

    info = StructInfo(
        None,
        fields,
        align=template.align,
        volatile=template.volatile,
        is_union=template.is_union,
        packed=template.packed,
    )

    gen.structs[canonical] = info

    # the substituted fields mix the template's names with the using
    # file's arguments, so no single view gates them
    gen.ungated_types += 1
    try:
        for field in fields:
            field.type = expand_alias(gen, field.type, seen)
            if is_reference(field.type):
                raise TypeError(f"field {field.name!r} cannot be a reference")

        for field in fields:
            validate_type(field.type, gen.structs)

        # the template's interface claims carry to each instance, its
        # arguments substituted in: 'List<T>: Iterable<T>' makes
        # 'List<i32>' implement 'Iterable<i32>'
        from siec.codegen.interfaces import constraints_hold, declare_implements

        if template.interfaces:
            declare_implements(gen, canonical, base,
                               [substitute(s, mapping) for s in template.interfaces],
                               template.line, template.file)

        # An '@extend Base<T>' claim has its own template environment. Unlike
        # claims written on the struct, it applies only when this instance's
        # arguments satisfy the extension bounds.
        claim_key = (
            base if gen.generic_structs.get(base) is template
            else f"{base}#{len(args)}"
        )
        for claims, constraints, file, line in gen.generic_struct_claims.get(
                claim_key, ()):
            if constraints_hold(gen, constraints, mapping, file):
                declare_implements(
                    gen,
                    canonical,
                    base,
                    [substitute(spelling, mapping) for spelling in claims],
                    line,
                    file,
                )
    finally:
        gen.ungated_types -= 1

    return canonical


def resolve_generic_function(gen: CodeGenerator, fn) -> None:
    """
    Resolve a generic function template, instantiated by its calls; a
    same-named template with a different type-parameter count joins as
    an arity overload, picked per call.
    """
    with source_location(line=fn.line, file=fn.file):
        if fn.name == "main":
            raise TypeError("'main' cannot be generic: the C runtime "
                            "calls it directly")

        # a removed template has nothing left to stamp: its name is
        # recorded so uses of it fail with the advice
        if fn.removed is not None:
            gen.removed[fn.name] = fn.removed
            return

        if fn.body is None and fn.asm is None:
            raise TypeError(f"generic function {fn.name!r} needs a body: "
                            "there is nothing to declare without one")

        primary = gen.generic_functions.get(fn.name)
        if primary is not None:
            # a same-named template joins as an overload: another
            # type-parameter count, or the same count over a different
            # parameter list; respelling both is a redeclaration
            overloads = gen.generic_overloads.setdefault(fn.name, [])
            for other in (primary, *overloads):
                if (len(other.type_params) == len(fn.type_params)
                        and template_identity(other) == template_identity(fn)):
                    if fn.is_override and not other.is_override:
                        if template_return(other) != template_return(fn):
                            from siec.codegen.overloads import shown_signature
                            raise TypeError(
                                f"function '{shown_signature(fn)}' has no "
                                "matching declaration to override")
                        overloads.append(fn)
                        return
                    from siec.codegen.overloads import shown_signature
                    what = ("is overridden more than once"
                            if fn.is_override else "is declared more than once")
                    raise TypeError(f"function '{shown_signature(fn)}' "
                                    f"{what}")

            if fn.is_override:
                targets = [
                    other for other in (primary, *overloads)
                    if (not other.is_override
                        and len(other.type_params) == len(fn.type_params)
                        and template_key(other) == template_key(fn)
                        and template_return(other) == template_return(fn))
                ]
                if not targets:
                    from siec.codegen.overloads import shown_signature
                    raise TypeError(f"function '{shown_signature(fn)}' "
                                    "has no matching declaration to override")

            overloads.append(fn)
            return

        if fn.is_override:
            from siec.codegen.overloads import shown_signature
            raise TypeError(f"function '{shown_signature(fn)}' "
                            "has no matching declaration to override")

        gen.generic_functions[fn.name] = fn


def template_key(template) -> tuple:
    """
    The signature identity of a template's parameter list: its types
    behind 'const', the type parameters normalized to their positions so
    'f<T>(v: T)' and 'f<U>(v: U)' spell the same shape.
    """
    mapping = {p: f"#{i}" for i, p in enumerate(template.type_params)}
    return tuple(substitute(strip_const(p.type), mapping)
                 for p in template.params)


def template_identity(template) -> tuple:
    """
    A generic template's normalized parameter shape and bounds. Bounds
    distinguish otherwise identical overloads: 'f<T>' and 'f<T: I>'.
    """
    mapping = {p: f"#{i}" for i, p in enumerate(template.type_params)}
    constraints = tuple(
        (mapping[param], substitute(bound, mapping))
        for param, value in (template.constraints or {}).items()
        for bound in constraint_bounds(value)
    )
    return template_key(template), constraints


def template_return(template) -> str | None:
    """A generic template's normalized return type."""
    if template.return_type is None:
        return None

    mapping = {p: f"#{i}" for i, p in enumerate(template.type_params)}
    return substitute(strip_const(template.return_type), mapping)


def rewrite_types(node, apply) -> None:
    """
    Walk an AST subtree, applying a rewrite to every type annotation in
    place: 'let x: T', casts, sizeofs, parameters, returns, and nested
    explicit type arguments.
    """
    if isinstance(node, (list, tuple)):
        for item in node:
            rewrite_types(item, apply)
        return

    if not is_dataclass(node):
        return

    for field in dataclass_fields(node):
        value = getattr(node, field.name)

        if isinstance(value, str):
            if (field.name in ("type", "return_type")
                    or (isinstance(node, (SizeOf, TypeId, TypeName))
                        and field.name == "name")):
                setattr(node, field.name, apply(value))
        elif field.name == "type_args" and value is not None:
            setattr(node, field.name, [apply(v) for v in value])
        elif (field.name in ("constraints", "receiver_constraints")
              and value is not None):
            setattr(node, field.name, {
                key: (tuple(apply(bound) for bound in bounds)
                      if isinstance(bounds, tuple) else apply(bounds))
                for key, bounds in value.items()
            })
        else:
            rewrite_types(value, apply)


def substitute_types(node, mapping: dict) -> None:
    """
    Walk an AST subtree, substituting type parameters into every type
    annotation in place, whole identifiers only: 'T*' becomes 'i32*',
    'Tx' stays itself.
    """
    rewrite_types(node, lambda value: substitute(value, mapping))


def respell_types(node, spelling: str, concrete: str) -> None:
    """
    Walk an AST subtree, replacing whole occurrences of one full type
    spelling - generic arguments included, so 'Iterable<char>' respells
    without touching other 'Iterable' instantiations.
    """
    pattern = re.compile(rf"(?<!\w){re.escape(spelling)}(?![\w<])")
    rewrite_types(node, lambda value: pattern.sub(concrete, value))


def unify(pattern: str | None, concrete: str | None,
          type_params: list, bindings: dict) -> None:
    """
    Match a parameter's type pattern against an argument's concrete type,
    binding each type parameter the pattern names.

    Structural mismatches bind nothing - argument coercion reports them
    with the instantiated types - but two arguments demanding different
    bindings for one parameter conflict here.
    """
    if pattern is None or concrete is None:
        return

    pattern, concrete = strip_const(pattern), strip_const(concrete)
    pattern, concrete = strip_reference(pattern), strip_reference(concrete)

    if pattern in type_params:
        previous = bindings.setdefault(pattern, concrete)
        if previous != concrete:
            raise TypeError(f"conflicting type arguments for {pattern!r}: "
                            f"{previous!r} and {concrete!r}")
        return

    if pattern.endswith("*") and concrete.endswith("*"):
        return unify(pattern[:-1], concrete[:-1], type_params, bindings)

    if pattern.endswith("[]") and concrete.endswith("[]"):
        return unify(pattern[:-2], concrete[:-2], type_params, bindings)

    raw_p, raw_c = raw_array(pattern), raw_array(concrete)
    if raw_p is not None and raw_c is not None:
        return unify(raw_p[0], raw_c[0], type_params, bindings)

    if ((pattern.startswith("fn(") and concrete.startswith("fn("))
            or (pattern.startswith("closure fn(")
                and concrete.startswith("closure fn("))):
        p_params, p_ret, _ = fn_type_parts(pattern)
        c_params, c_ret, _ = fn_type_parts(concrete)
        for p, c in zip(p_params, c_params):
            unify(p, c, type_params, bindings)
        return unify(p_ret, c_ret, type_params, bindings)

    generic_p, generic_c = split_generic(pattern), split_generic(concrete)
    if (generic_p is not None and generic_c is not None
            and generic_p[0] == generic_c[0]
            and len(generic_p[1]) == len(generic_c[1])):
        for p, c in zip(generic_p[1], generic_c[1]):
            unify(p, c, type_params, bindings)


def resolve_generic_call(gen: CodeGenerator, template, call, scope: dict,
                         expected: str | None = None) -> list:
    """
    The type arguments of a generic call: the explicit '<...>' list, or
    each parameter's pattern unified with its argument's type - the
    expected result type driving inference where arguments cannot:
    'return Ok(v);' binds V and E from the declared return type.
    """
    from siec.codegen.aliases import expand_alias

    if call.type_args is not None:
        args = [expand_alias(gen, arg) for arg in call.type_args]
        if len(args) != len(template.type_params):
            take = len(template.type_params)
            raise TypeError(f"generic function {template.name!r} takes {take} "
                            f"type argument{'s' if take != 1 else ''}, "
                            f"got {len(args)}")
        return args

    # literal arguments default like they do in any untyped context, so
    # 'pick(3, 9)' binds T to i32 the way 'let x = 3;' would
    from siec.codegen.inference import (
        adapts_in_arithmetic,
        infer_type,
        untyped_reason,
    )

    bindings: dict = {}
    if expected is not None and template.return_type is not None:
        unify(template.return_type, expected, template.type_params, bindings)

    # arguments fill what the expected type left unbound; where both
    # speak, the declared type wins and the argument coerces to it
    arguments = []
    for param, arg in zip(template.params, call.args):
        concrete = infer_type(gen, arg, scope)
        if concrete is None:
            # An unbound parameter is only the consequence when the
            # argument itself names nothing. Keep the primary diagnostic.
            if (reason := untyped_reason(gen, arg, scope)) is not None:
                raise reason
        arguments.append((param, arg, concrete))

    # Declared arguments pin placeholders before adaptive numeric literals.
    # Thus `same(typed_u64, 0)` infers u64 and checks the zero against it,
    # while `same(0, 1)` still defaults its otherwise-unbound T to i32.
    arguments.sort(key=lambda entry: adapts_in_arithmetic(
        gen, entry[1], scope))

    inferred: dict = {}
    for param, arg, concrete in arguments:
        adaptive = adapts_in_arithmetic(gen, arg, scope)
        try:
            unify(param.type, concrete, template.type_params, inferred)
        except TypeError:
            if adaptive:
                continue
            if not bindings:
                raise

    for name, value in inferred.items():
        bindings.setdefault(name, value)

    # an aggregate literal has no type of its own, so it leaves an
    # interface placeholder unbound; the array family's claim gives it
    # the array reading - '{ptr, len}' against 'Iterable<char>' is a
    # 'char[]', arrays being iterable by definition
    constraints = template.constraints or {}
    for param, arg in zip(template.params, call.args):
        placeholder = strip_const(strip_reference(strip_const(param.type)))
        if (placeholder in bindings or placeholder not in constraints
                or not isinstance(arg, (AggregateLiteral, ArrayLiteral))):
            continue

        from siec.codegen.interfaces import constraints_hold

        for constraint in constraint_bounds(constraints[placeholder]):
            required = substitute(constraint, bindings)
            for entry in gen.array_claims:
                family_param, claim, family_constraints, file = entry
                family: dict = {}
                unify(claim, required, [family_param], family)
                if (family_param in family
                        and constraints_hold(
                            gen, family_constraints, family, file)):
                    bindings[placeholder] = f"{family[family_param]}[]"
                    break
            if placeholder in bindings:
                break

    infer_constraint_arguments(gen, template, bindings)

    missing = [p for p in template.type_params if p not in bindings]
    if missing:
        named = ", ".join(map(repr, missing))
        raise TypeError(f"cannot infer type argument{'s' if len(missing) != 1 else ''} "
                        f"{named} for generic function {template.name!r}: spell "
                        f"them explicitly, '{template.name}<...>(...)'")

    return [bindings[p] for p in template.type_params]


def infer_constraint_arguments(gen: CodeGenerator, template,
                               bindings: dict) -> None:
    """
    Infer free type arguments from a bound constrained parameter.

    Interface adaptation turns ``Iterable<T>`` into a concrete placeholder
    constrained by ``Iterable<T>``. If that placeholder binds to ``char[]``,
    its concrete ``Iterable<char>`` claim supplies the otherwise-hidden
    ``T = char`` binding.
    """
    from siec.codegen.interfaces import claimed_interfaces

    constraints = template.constraints or {}
    while True:
        before = dict(bindings)

        for placeholder, value in constraints.items():
            concrete = bindings.get(placeholder)
            if concrete is None:
                continue

            for constraint in constraint_bounds(value):
                required = substitute(constraint, bindings)
                required_base = (split_generic(required) or (required, []))[0]
                possibilities = []

                for claim in claimed_interfaces(gen, concrete):
                    claim_base = (split_generic(claim) or (claim, []))[0]
                    if claim_base != required_base:
                        continue

                    trial = dict(bindings)
                    try:
                        unify(required, claim, template.type_params, trial)
                    except TypeError:
                        continue

                    additions = {
                        name: trial[name]
                        for name in template.type_params
                        if name not in bindings and name in trial
                    }
                    if additions not in possibilities:
                        possibilities.append(additions)

                # A type may claim several instantiations of one interface.
                # Only infer when those claims agree; otherwise the call must
                # spell the otherwise-hidden type argument.
                if len(possibilities) == 1:
                    bindings.update(possibilities[0])

        if bindings == before:
            return


def accepts_arity(template, count: int) -> bool:
    """
    Whether a template's parameter list can take a call's argument count,
    trailing defaults making their parameters optional.
    """
    params = template.params
    required = len(params)
    while required and params[required - 1].default is not None:
        required -= 1

    if template.variadic:
        required = min(required, len(params) - 1)
        return required <= count

    return required <= count and (count <= len(params) or template.var_arg)


def pick_generic_call(gen: CodeGenerator, symbol: str, call, scope: dict,
                      expected: str | None = None,
                      method_receiver=None) -> tuple:
    """
    Resolve a call against a generic function's templates - arity
    overloads included - returning the winning template and its type
    arguments. The call's shape filters the candidates; the first that
    resolves wins.
    """
    candidates = [t for t in (gen.generic_functions.get(symbol),
                              *gen.generic_overloads.get(symbol, ()))
                  if t is not None]

    failure = None
    resolved = []
    for template in candidates:
        candidate_call = call
        if method_receiver is not None:
            from siec.codegen.methods import template_takes_receiver

            if template_takes_receiver(template):
                candidate_call = Call(
                    call.name,
                    [method_receiver, *call.args],
                    call.type_args,
                )

        if (call.type_args is not None
                and len(call.type_args) != len(template.type_params)):
            continue

        if not accepts_arity(template, len(candidate_call.args)):
            continue

        try:
            type_args = resolve_generic_call(gen, template, candidate_call, scope,
                                             expected)

            # a template whose constraints reject the bound arguments is
            # not a candidate; its sibling may still take the call
            if template.constraints:
                from siec.codegen.aliases import expand_alias
                from siec.codegen.interfaces import check_constraints

                gen.ungated_types += 1
                try:
                    expanded = [expand_alias(gen, arg) for arg in type_args]
                finally:
                    gen.ungated_types -= 1

                check_constraints(gen, template,
                                  dict(zip(template.type_params, expanded)))

            resolved.append((template, type_args, candidate_call))
        except TypeError as error:
            failure = failure or error

    if len(resolved) == 1:
        return resolved[0][:2]

    # several resolve: a typed context picks the templates whose returns
    # produce it, then the arguments' concrete types rank the substituted
    # signatures like any overload. At equal conversion strength, the more
    # constrained template is the more specific one; a remaining tie, or no
    # ranked fit at all, keeps declaration order.
    if resolved:
        if expected is not None:
            matching = [entry for entry in resolved
                        if returns_expected(gen, *entry[:2], expected)]
            resolved = matching or resolved

        ranked = [
            (*fit.rank, -constraint_count(entry[0].constraints),
             -int(entry[0].is_override), entry)
            for entry in resolved
            if (fit := generic_fit(
                    gen, entry[0], entry[1], entry[2], scope)) is not None
        ]
        if ranked:
            best = min(candidate[:5] for candidate in ranked)
            winners = [
                candidate for candidate in ranked
                if candidate[:5] == best
            ]
            if sum(candidate[5][0].is_override
                   for candidate in winners) > 1:
                raise TypeError(
                    f"overrides of function {symbol!r} are ambiguous")
            return winners[0][5][:2]

        return resolved[0][:2]

    if failure is not None:
        raise failure

    # nothing fit the call's shape: report against the primary template
    candidate_call = call
    if method_receiver is not None:
        from siec.codegen.methods import template_takes_receiver

        if template_takes_receiver(candidates[0]):
            candidate_call = Call(
                call.name,
                [method_receiver, *call.args],
                call.type_args,
            )
    return candidates[0], resolve_generic_call(
        gen, candidates[0], candidate_call, scope, expected)


def returns_expected(gen: CodeGenerator, template, type_args: list,
                     expected: str) -> bool:
    """
    Whether a template's substituted return produces the context's
    expected type.
    """
    # deferred import: aliases and generics are mutually recursive
    from siec.codegen.aliases import expand_alias

    if template.return_type is None:
        return False

    spelled = substitute(template.return_type,
                         dict(zip(template.type_params, type_args)))

    # the substituted spellings mix files' names; no view gates them
    gen.ungated_types += 1
    try:
        return (strip_const(expand_alias(gen, spelled))
                == strip_const(expand_alias(gen, expected)))
    finally:
        gen.ungated_types -= 1


def generic_fit(gen: CodeGenerator, template, type_args: list, call,
                scope: dict) -> str | None:
    """
    How a template's substituted parameters take a call's arguments,
    ranked like a concrete overload's fits; None when one cannot. An
    'args...' pack and trailing defaults rank by the fixed prefix.
    """
    # deferred imports: aliases and overloads both lean on generics
    from siec.codegen.aliases import expand_alias
    from siec.codegen.overloads import FitProfile, parameter_fit, rank_type

    mapping = dict(zip(template.type_params, type_args))
    params = [substitute(p.type, mapping) for p in template.params]

    required = len(template.params)
    while required and template.params[required - 1].default is not None:
        required -= 1
    if template.variadic:
        required = min(required, len(params) - 1)
    if (len(call.args) < required
            or (len(call.args) > len(params)
                and not template.var_arg and not template.variadic)):
        return None

    if template.variadic:
        params = params[:-1]

    args = list(call.args)[:len(params)]
    params = params[:len(args)]

    # the substituted spellings mix files' names; no view gates them
    gen.ungated_types += 1
    try:
        adopted = 0
        implicit = 0
        for arg, param in zip(args, params):
            one = parameter_fit(gen, arg, rank_type(gen, arg, scope),
                                expand_alias(gen, param))
            if one is None:
                return None

            if one == "adopt":
                adopted += 1
            elif one == "implicit":
                implicit += 1

        return FitProfile(adopted, implicit)
    finally:
        gen.ungated_types -= 1


def pick_call_candidate(gen: CodeGenerator, symbol: str, call, scope: dict,
                        expected: str | None = None, *,
                        module: str | None = None,
                        receiver: str | None = None,
                        generic_call=None,
                        generic_scope: dict | None = None,
                        method_receiver=None) -> tuple[str, object]:
    """
    Arbitrate concrete and generic overloads by their actual fit strength.

    Exact beats implicit, and implicit beats literal adoption.  A concrete
    candidate wins a tie, preserving the more specific non-template choice,
    but merely being concrete no longer lets an implicit array decay bypass
    an exact constrained/interface match.

    ``receiver`` supplies a constructor's not-yet-materialized receiver to
    concrete ranking.  Its ``generic_call`` counterpart carries the synthetic
    receiver expression used to infer and rank a generic constructor.
    """
    from siec.codegen.overloads import pick_overload_fit

    concrete = None
    concrete_error = None

    # Explicit type arguments request a generic spelling; a same-named
    # concrete overload must not intercept it, exact or otherwise.
    explicit_call = generic_call or call
    if explicit_call.type_args is None and symbol in gen.overloads:
        try:
            concrete_symbol, concrete_fit = pick_overload_fit(
                gen, symbol, call.args, scope,
                receiver=receiver, module=module,
                method_receiver=method_receiver)
            concrete = (concrete_symbol, concrete_fit)
        except TypeError as error:
            concrete_error = error

    # Nothing can outrank an exact concrete overload, so avoid resolving
    # irrelevant templates (which may intentionally be uninferable here).
    if concrete is not None and concrete[1].tier == "exact":
        return "concrete", concrete[0]

    generic = None
    generic_error = None
    if gen.generic_functions.get(symbol) is not None:
        ranked_call = generic_call or call
        ranked_scope = generic_scope if generic_scope is not None else scope
        try:
            template, type_args = pick_generic_call(
                gen, symbol, ranked_call, ranked_scope, expected,
                method_receiver=method_receiver)
            fitted_call = ranked_call
            if method_receiver is not None:
                from siec.codegen.methods import template_takes_receiver

                if template_takes_receiver(template):
                    fitted_call = Call(
                        ranked_call.name,
                        [method_receiver, *ranked_call.args],
                        ranked_call.type_args,
                    )
            fit = generic_fit(
                gen, template, type_args, fitted_call, ranked_scope)
            generic = (template, type_args, fit)
        except TypeError as error:
            generic_error = error

    if concrete is not None and generic is not None:
        if generic[2] is not None and generic[2].rank < concrete[1].rank:
            return "generic", generic[:2]
        return "concrete", concrete[0]

    if concrete is not None:
        return "concrete", concrete[0]
    if generic is not None:
        return "generic", generic[:2]

    if generic_error is not None:
        raise generic_error
    if concrete_error is not None:
        raise concrete_error
    return "plain", symbol


def instantiate_function(gen: CodeGenerator, template, type_args: list) -> str:
    """
    Instantiate a generic function for one argument list, declaring it
    under its canonical symbol and queuing its body for emission; every
    call spelling the same arguments shares the one instance.
    """
    from siec.codegen.aliases import expand_alias

    # the arguments arrive expanded (an explicit spelling, resolved at the
    # call) or as carried canonical names (inferred from the values), so
    # no file's view gates this second pass
    gen.ungated_types += 1
    try:
        type_args = [expand_alias(gen, arg) for arg in type_args]
    finally:
        gen.ungated_types -= 1

    for arg in type_args:
        if arg.startswith("const ") or arg.startswith("&"):
            raise TypeError(f"cannot instantiate {template.name!r} with "
                            f"{arg!r}: the argument carries a modifier")

    # an interface-constrained parameter only takes an implementing type
    if template.constraints:
        from siec.codegen.interfaces import check_constraints

        check_constraints(gen, template,
                          dict(zip(template.type_params, type_args)))

    symbol = f"{template.name}<{','.join(type_args)}>"

    # same-count sibling templates would share the spelling: the declared
    # parameter list joins the symbol to keep each instance its own, the
    # same in every unit since it comes from the template's source
    siblings = (gen.generic_functions.get(template.name),
                *gen.generic_overloads.get(template.name, ()))
    if any(other is not None and other is not template
           and len(other.type_params) == len(template.type_params)
           for other in siblings):
        params, constraints = template_identity(template)
        shape = [*params, *(f"{param}:{bound}"
                            for param, bound in constraints)]
        symbol = f"{symbol}({','.join(shape)})"

    # a '@static' template's instances stay file-local like the template:
    # the symbol mangles per file, so another file's same-named static
    # neither sees nor collides with it
    if template.is_static:
        key = (template.file, symbol)
        if key not in gen.statics:
            gen.statics[key] = f"{symbol}.static.{len(gen.statics)}"

        symbol = gen.statics[key]

    if symbol not in gen.instantiated_functions:
        if gen.semantic_complete:
            raise RuntimeError(
                "LLVM emission attempted to instantiate a generic function")

        gen.instantiated_functions.add(symbol)
        gen.function_instance_states[symbol] = "requested"
        if gen.current_function is not None:
            gen.instantiation_sites.setdefault(
                symbol,
                (gen.current_function, gen.current_file, gen.current_line),
            )

        instance = copy.deepcopy(template)
        instance.name = symbol
        instance.type_params = None
        substitute_types(instance, dict(zip(template.type_params, type_args)))

        # the canonical instance name is already unique per signature:
        # it stays the module symbol, unmangled, like an '@symbol' pick
        instance.symbol = symbol

        from siec.codegen.worklist import resolve_function_instance

        resolve_function_instance(gen, instance)

    return symbol


def reference_template(gen: CodeGenerator, name: str):
    """
    The template a reference names: a dotted name resolves through its
    module binding, an unqualified one must be visible to this file.

    None when the name resolves to something that isn't a template.
    """
    if "." in name:
        symbol = gen.resolve_qualified(name.split("."))
        if symbol is None:
            raise NameError(f"undefined function {name!r}")
    else:
        if not gen.sees(name):
            raise NameError(f"undefined function {name!r}")

        symbol = gen.resolve_symbol(name)

    return gen.generic_functions.get(symbol)


def emit_generic_reference(gen: CodeGenerator, expr) -> object:
    """
    The function value of an explicit 'f<i32>' reference: the instance,
    declared on first use like any generic call's.
    """
    from siec.codegen.deprecation import note_use

    template = reference_template(gen, expr.name)
    if template is None:
        raise TypeError(f"function {expr.name!r} is not generic")

    symbol = instantiate_function(gen, template, expr.type_args)

    # handing the instance around reaches it as surely as calling it
    note_use(gen, symbol)
    return gen.module.globals[symbol]


def reference_for_target(gen: CodeGenerator, expr, target_name: str):
    """
    The function value of a bare generic name bound to a function-typed
    context: 'let f: fn(i32) -> i32 = identity;' unifies the template's
    signature with the target to pick the instance. None when the name
    isn't a template's; the caller falls through to normal emission.
    """
    template = gen.generic_functions.get(gen.resolve_symbol(expr.name))
    if template is None:
        return None

    return bind_to_target(gen, template, expr.name, target_name)


def bind_to_target(gen: CodeGenerator, template, name: str, target_name: str):
    """
    Instantiate a template for a function-typed target by unifying the
    signatures, returning the instance's function value.
    """
    params, ret, suffix = fn_type_parts(target_name)
    if suffix:
        return None

    if len(params) != len(template.params):
        take = len(template.params)
        raise TypeError(f"cannot bind generic function {name!r} to "
                        f"{target_name!r}: it takes {take} "
                        f"parameter{'s' if take != 1 else ''}")

    bindings: dict = {}
    for param, concrete in zip(template.params, params):
        unify(param.type, concrete, template.type_params, bindings)
    unify(template.return_type, ret, template.type_params, bindings)

    missing = [p for p in template.type_params if p not in bindings]
    if missing:
        named = ", ".join(map(repr, missing))
        raise TypeError(f"cannot infer type argument{'s' if len(missing) != 1 else ''} "
                        f"{named} for generic function {name!r} from "
                        f"{target_name!r}: spell them, '{name}<...>'")

    from siec.codegen.deprecation import note_use

    type_args = [bindings[p] for p in template.type_params]
    symbol = instantiate_function(gen, template, type_args)

    # handing the instance around reaches it as surely as calling it
    note_use(gen, symbol)
    return gen.module.globals.get(symbol, symbol)


def reference_type(gen: CodeGenerator, expr) -> str | None:
    """
    The canonical function type of an explicit 'f<i32>' reference, for
    inference; None when the name isn't a template's.
    """
    from siec.codegen.aliases import expand_alias

    try:
        template = reference_template(gen, expr.name)
    except NameError:
        return None

    if template is None:
        return None

    args = [expand_alias(gen, arg) for arg in expr.type_args]
    mapping = dict(zip(template.type_params, args))

    params = ",".join(expand_alias(gen, substitute(p.type, mapping))
                      for p in template.params)
    name = f"fn({params})"
    if template.return_type is not None:
        name += f"->{expand_alias(gen, substitute(template.return_type, mapping))}"

    return name
