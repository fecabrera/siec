"""Monomorphization of generics.

A 'struct S<T>' or 'fn f<T>' declaration registers a template; each
concrete spelling - 'S<i32>' in a type position, 'f(x)' or 'f<i32>(x)'
at a call - stamps out a real struct or function under its canonical
name, so every use of the same arguments shares one instantiation.
"""

import copy
import re
from dataclasses import fields as dataclass_fields, is_dataclass

from siec.ast import Call, Field, SizeOf
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator, StructInfo
from siec.codegen.types import (
    fn_type_parts,
    is_reference,
    raw_array,
    resolve_type,
    strip_const,
    strip_reference,
)

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
INSTANTIATION = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)<")


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
    from siec.codegen.structs import union_storage

    if (parts := split_generic(name)) is None:
        return None

    base, args = parts
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

    args = [expand_alias(gen, arg, seen) for arg in args]
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

    if template.fields is None:
        raise TypeError(f"generic struct {base!r} is declared without a body")

    ident = gen.module.context.get_identified_type(canonical)
    mapping = dict(zip(template.params, args))

    # fields deep-copy so each instantiation owns its types and defaults
    fields = copy.deepcopy(template.fields)
    substitute_types(fields, mapping)

    info = StructInfo(ident, fields, align=template.align,
                      volatile=template.volatile, is_union=template.is_union)
    if template.packed:
        ident.packed = True

    gen.structs[canonical] = info

    # the substituted fields mix the template's names with the using
    # file's arguments, so no single view gates them
    gen.ungated_types += 1
    try:
        for field in fields:
            field.type = expand_alias(gen, field.type, seen)
            if is_reference(field.type):
                raise TypeError(f"field {field.name!r} cannot be a reference")

        resolved = [resolve_type(f.type, gen.structs) for f in fields]
        if info.is_union:
            resolved = union_storage(gen, resolved)

        ident.set_body(*resolved)

        # the template's interface claims carry to each instance, its
        # arguments substituted in: 'List<T>: Iterable<T>' makes
        # 'List<i32>' implement 'Iterable<i32>'
        if template.interfaces:
            from siec.codegen.interfaces import declare_implements

            declare_implements(gen, canonical, base,
                               [substitute(s, mapping) for s in template.interfaces],
                               template.line, template.file)
    finally:
        gen.ungated_types -= 1

    return canonical


def register_generic_function(gen: CodeGenerator, fn) -> None:
    """
    Register a generic function template, instantiated by its calls; a
    same-named template with a different type-parameter count joins as
    an arity overload, picked per call.
    """
    with source_location(line=fn.line, file=fn.file):
        if fn.name == "main":
            raise TypeError("'main' cannot be generic: the C runtime "
                            "calls it directly")

        if fn.body is None and fn.asm is None:
            raise TypeError(f"generic function {fn.name!r} needs a body: "
                            "there is nothing to declare without one")

        primary = gen.generic_functions.get(fn.name)
        if primary is not None:
            overloads = gen.generic_overloads.setdefault(fn.name, [])
            arities = {len(t.type_params) for t in (primary, *overloads)}
            if len(fn.type_params) in arities:
                raise TypeError(f"function {fn.name!r} is declared more "
                                "than once")

            overloads.append(fn)
            return

        gen.generic_functions[fn.name] = fn


def substitute_types(node, mapping: dict) -> None:
    """
    Walk an AST subtree, substituting type parameters into every type
    annotation in place: 'let x: T', casts, sizeofs, parameters, returns,
    and nested explicit type arguments.
    """
    if isinstance(node, (list, tuple)):
        for item in node:
            substitute_types(item, mapping)
        return

    if not is_dataclass(node):
        return

    for field in dataclass_fields(node):
        value = getattr(node, field.name)

        if isinstance(value, str):
            if (field.name in ("type", "return_type")
                    or (isinstance(node, SizeOf) and field.name == "name")):
                setattr(node, field.name, substitute(value, mapping))
        elif field.name == "type_args" and value is not None:
            setattr(node, field.name, [substitute(v, mapping) for v in value])
        elif field.name == "constraints" and value is not None:
            setattr(node, field.name,
                    {k: substitute(v, mapping) for k, v in value.items()})
        else:
            substitute_types(value, mapping)


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

    if pattern.startswith("fn(") and concrete.startswith("fn("):
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
    from siec.codegen.inference import infer_type

    bindings: dict = {}
    if expected is not None and template.return_type is not None:
        unify(template.return_type, expected, template.type_params, bindings)

    # arguments fill what the expected type left unbound; where both
    # speak, the declared type wins and the argument coerces to it
    inferred: dict = {}
    for param, arg in zip(template.params, call.args):
        try:
            unify(param.type, infer_type(gen, arg, scope),
                  template.type_params, inferred)
        except TypeError:
            if not bindings:
                raise

    for name, value in inferred.items():
        bindings.setdefault(name, value)

    missing = [p for p in template.type_params if p not in bindings]
    if missing:
        named = ", ".join(map(repr, missing))
        raise TypeError(f"cannot infer type argument{'s' if len(missing) != 1 else ''} "
                        f"{named} for generic function {template.name!r}: spell "
                        f"them explicitly, '{template.name}<...>(...)'")

    return [bindings[p] for p in template.type_params]


def accepts_arity(template, count: int) -> bool:
    """
    Whether a template's parameter list can take a call's argument count,
    trailing defaults making their parameters optional.
    """
    params = template.params
    required = len(params)
    while required and params[required - 1].default is not None:
        required -= 1

    return required <= count and (count <= len(params) or template.var_arg)


def pick_generic_call(gen: CodeGenerator, symbol: str, call, scope: dict,
                      expected: str | None = None) -> tuple:
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
    for template in candidates:
        if (call.type_args is not None
                and len(call.type_args) != len(template.type_params)):
            continue

        if not accepts_arity(template, len(call.args)):
            continue

        try:
            return template, resolve_generic_call(gen, template, call, scope,
                                                  expected)
        except TypeError as error:
            failure = failure or error

    if failure is not None:
        raise failure

    # nothing fit the call's shape: report against the primary template
    return candidates[0], resolve_generic_call(gen, candidates[0], call,
                                               scope, expected)


def instantiate_function(gen: CodeGenerator, template, type_args: list) -> str:
    """
    Instantiate a generic function for one argument list, declaring it
    under its canonical symbol and queuing its body for emission; every
    call spelling the same arguments shares the one instance.
    """
    from siec.codegen.aliases import expand_alias
    from siec.codegen.functions import declare_function

    type_args = [expand_alias(gen, arg) for arg in type_args]
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
    if symbol not in gen.instantiated_functions:
        gen.instantiated_functions.add(symbol)

        instance = copy.deepcopy(template)
        instance.name = symbol
        instance.type_params = None
        substitute_types(instance, dict(zip(template.type_params, type_args)))

        # the instance's signature mixes files' names; no view gates it
        gen.ungated_types += 1
        try:
            declare_function(gen, instance)
        finally:
            gen.ungated_types -= 1

        gen.pending_functions.append(instance)

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
    template = reference_template(gen, expr.name)
    if template is None:
        raise TypeError(f"function {expr.name!r} is not generic")

    return gen.module.globals[instantiate_function(gen, template, expr.type_args)]


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

    type_args = [bindings[p] for p in template.type_params]
    return gen.module.globals[instantiate_function(gen, template, type_args)]


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
