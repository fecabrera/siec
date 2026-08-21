"""Resolution of struct methods.

A method is a function named 'S::m'. One whose first parameter is its
'&S' (or 'const &S') receiver is an instance method: 'S::method(s)'
calls it like any function, and 's.method()' passes the receiver
implicitly. Any other first parameter makes it static: no instance
joins the arguments, from either spelling. A generic struct's methods
are templates, stamped per instantiation like the struct itself.
"""

import copy

from llvmlite import ir

from siec.ast import Call, Member, Var
from siec.codegen.errors import source_location
from siec.codegen.generator import CodeGenerator
from siec.codegen.generics import (constraint_count, split_generic, substitute,
                                   substitute_types)
from siec.codegen.types import strip_const, strip_reference


def method_signature(fn, mapping: dict | None = None) -> tuple:
    """A method's parameter and return types after an optional substitution."""
    mapping = mapping or {}
    params = tuple(
        strip_const(substitute(param.type, mapping))
        for param in fn.params
    )
    ret = (strip_const(substitute(fn.return_type, mapping))
           if fn.return_type is not None else None)
    return params, ret


def normalized_method_signature(fn) -> tuple:
    """A receiver template's signature independent of placeholder names."""
    mapping = {
        param: f"#R{index}"
        for index, param in enumerate(fn.receiver_params or ())
    }
    mapping.update({
        param: f"#T{index}"
        for index, param in enumerate(fn.type_params or ())
    })
    return method_signature(fn, mapping)


def method_family_identity(fn) -> tuple:
    """
    A receiver template's signature and bounds. Bounds distinguish
    otherwise identical families: 'T: SignedInteger' and 'T: UnsignedInteger'.
    """
    from siec.codegen.generics import constraint_bounds

    mapping = {
        param: f"#R{index}"
        for index, param in enumerate(fn.receiver_params or ())
    }
    mapping.update({
        param: f"#T{index}"
        for index, param in enumerate(fn.type_params or ())
    })
    constraints = tuple(
        (mapping.get(param, param), substitute(bound, mapping))
        for param, value in sorted((fn.receiver_constraints or {}).items())
        for bound in constraint_bounds(value)
    )
    return method_signature(fn, mapping), constraints


def inherit_receiver_constraints(target, source) -> None:
    """Merge bounds carried by another declaration of one method family."""
    inherited = source.receiver_constraints or {}
    if target.receiver_constraints is None:
        target.receiver_constraints = copy.deepcopy(inherited)
        return

    for param, bound in inherited.items():
        previous = target.receiver_constraints.get(param)
        bounds = previous if isinstance(previous, tuple) else (previous,)
        bounds += bound if isinstance(bound, tuple) else (bound,)
        ordered = tuple(sorted(value for value in set(bounds)
                               if value is not None))
        target.receiver_constraints[param] = (
            ordered[0] if len(ordered) == 1 else ordered)


def concrete_type_like(gen: CodeGenerator, spelling: str) -> bool:
    """Whether a receiver argument contains no free type placeholders."""
    from siec.codegen.interfaces import is_type_name

    spelling = strip_const(strip_reference(spelling))
    while spelling.endswith("*") or spelling.endswith("[]"):
        spelling = spelling[:-1] if spelling.endswith("*") else spelling[:-2]

    if (parts := split_generic(spelling)) is not None:
        return (is_type_name(gen, parts[0])
                and all(concrete_type_like(gen, arg) for arg in parts[1]))

    return is_type_name(gen, spelling)


def select_method_overrides(gen: CodeGenerator, base: str, method: str,
                            entries: list[tuple]) -> list[tuple]:
    """
    Pick one eligible implementation per instantiated method signature.

    An override replaces the ordinary family only where its bounds hold.
    Two equally specific overrides applying to the same receiver are
    ambiguous rather than depending on declaration order.
    """
    groups: dict[tuple, list] = {}
    for template, mapping in entries:
        groups.setdefault(method_signature(template, mapping), []).append(
            (template, mapping))

    selected = []
    for signature, candidates in groups.items():
        overrides = [
            entry for entry in candidates if entry[0].is_override
        ]
        if not overrides:
            # Ordinary templates with identical concrete signatures after
            # substitution cannot all stamp: keep the most specific bound,
            # and declaration order on a remaining tie.
            rank = max(constraint_count(template.receiver_constraints)
                       for template, _ in candidates)
            winners = [
                entry for entry in candidates
                if constraint_count(entry[0].receiver_constraints) == rank
            ]
            selected.append(winners[0])
            continue

        rank = max(constraint_count(template.receiver_constraints)
                   for template, _ in overrides)
        winners = [
            entry for entry in overrides
            if constraint_count(entry[0].receiver_constraints) == rank
        ]
        if len(winners) != 1:
            raise TypeError(f"overrides of method {base + '::' + method!r} "
                            "are ambiguous for receiver "
                            f"{base!r}")

        selected.append(winners[0])

    return selected


def resolve_method_declaration(gen: CodeGenerator, fn) -> None:
    """
    Resolve a method: a plain one declares like any function under its
    'S::m' name, a generic one like a generic function, and a generic
    struct's becomes a template stamped per struct instantiation.
    """
    from siec.codegen.functions import resolve_function
    from siec.codegen.generics import resolve_generic_function
    from siec.codegen.overloads import shown_signature

    with source_location(line=fn.line, file=fn.file):
        # A template environment may decorate an already-spelled generic
        # receiver, as in '@where<T> fn List<T>::f'. Normalize it to the
        # same family identity as the standalone 'fn List<T>::f' syntax.
        parts = split_generic(fn.receiver)
        if (fn.receiver_params is not None and parts is not None
                and parts[1] == fn.receiver_params):
            fn.receiver = parts[0]
            fn.name = f"{fn.receiver}::{fn.name.partition('::')[2]}"

        # The parser cannot know whether the element in 'X[]::m' is a
        # placeholder or a declared type. Settle it now that collection is
        # complete: 'char[]::m' is concrete, while 'T[]::m' stays a family.
        if (fn.receiver_params is not None and fn.receiver.endswith("[]")
                and fn.receiver_params == [fn.receiver[:-2]]):
            element = fn.receiver[:-2]
            if concrete_type_like(gen, element):
                fn.receiver_params = None

        # The same ambiguity exists in 'Box<X>::m': X is a template
        # placeholder when free and a concrete specialization argument when
        # it names a collected type.
        if (fn.receiver_params is not None
                and fn.receiver in gen.generic_structs
                and all(concrete_type_like(gen, param)
                        for param in fn.receiver_params)):
            receiver = f"{fn.receiver}<{','.join(fn.receiver_params)}>"
            fn.receiver = receiver
            fn.name = f"{receiver}::{fn.name.partition('::')[2]}"
            fn.receiver_params = None

        if fn.is_private:
            gen.private_methods.setdefault(fn.name, set()).add(fn.file)

        if fn.receiver_params is not None:
            # a removed template has nothing left to stamp: its name is
            # recorded so uses of it fail with the advice
            if fn.removed is not None:
                base = "[]" if fn.receiver.endswith("[]") else fn.receiver
                gen.removed[f"{base}::{fn.name.partition('::')[2]}"] = fn.removed
                return

            # A generic struct's method may overload like any other, its
            # templates stamped together per struct instantiation. An array's
            # ('T[]::m') registers under the one array family, whatever its
            # element placeholder is called.
            method = fn.name.partition("::")[2]
            if fn.receiver in fn.receiver_params:
                templates = gen.generic_receiver_methods.setdefault(
                    method, [])
            else:
                base = "[]" if fn.receiver.endswith("[]") else fn.receiver
                templates = gen.generic_methods.setdefault((base, method), [])

            same = [
                template for template in templates
                if normalized_method_signature(template)[0]
                == normalized_method_signature(fn)[0]
            ]
            if fn.is_override and not same:
                raise TypeError(f"method '{shown_signature(fn)}' "
                                "has no matching declaration to override")

            if same and not fn.is_override:
                same_sig = [
                    template for template in same
                    if (not template.is_override
                        and normalized_method_signature(template)
                        == normalized_method_signature(fn))
                ]
                exact = [
                    template for template in same_sig
                    if method_family_identity(template)
                    == method_family_identity(fn)
                ]

                # A nested declaration may carry the struct's receiver
                # bounds while an out-of-line body omits them (or the
                # reverse order). Same parameter list with only one side
                # defining still pairs; distinct bounds with two bodies
                # remain overloads.
                fn_defines = fn.body is not None or fn.asm is not None
                if not exact:
                    exact = [
                        template for template in same_sig
                        if ((template.body is not None or template.asm is not None)
                            != fn_defines)
                    ]

                if exact:
                    definitions = [
                        template for template in exact
                        if template.body is not None or template.asm is not None
                    ]
                    if fn_defines and definitions:
                        raise TypeError(f"method '{shown_signature(fn)}' "
                                        "is declared more than once")

                    if fn_defines:
                        # A body may live outside the struct after its nested
                        # declaration. Keep the body as the family template and
                        # carry across receiver bounds supplied by the struct.
                        for declaration in exact:
                            inherit_receiver_constraints(fn, declaration)
                        first = templates.index(exact[0])
                        templates[first] = fn
                        for declaration in exact[1:]:
                            templates.remove(declaration)
                    else:
                        # Definition collection is order-independent: a nested
                        # declaration seen after its out-of-line body still lends
                        # the struct's bounds to the retained template.
                        inherit_receiver_constraints(exact[0], fn)
                    return

                # Same parameter list with a different return type conflicts;
                # the same return with different receiver bounds is an
                # overload, like 'f<T>' versus 'f<T: I>'.
                if any(
                        not template.is_override
                        and normalized_method_signature(template)
                        != normalized_method_signature(fn)
                        for template in same):
                    raise TypeError(f"conflicting declarations for method "
                                    f"'{shown_signature(fn)}'")

            if same:

                targets = [
                    template for template in same
                    if (not template.is_override
                        and normalized_method_signature(template)
                        == normalized_method_signature(fn))
                ]
                if not targets:
                    raise TypeError(f"method '{shown_signature(fn)}' "
                                    "has no matching declaration to override")

                if any(
                        template.is_override
                        and template.receiver_constraints
                        == fn.receiver_constraints
                        for template in same):
                    raise TypeError(f"method '{shown_signature(fn)}' "
                                    "is overridden more than once")

            templates.append(fn)
        elif fn.type_params is not None:
            # a template registers under its name directly, so an alias
            # receiver joins its canonical name here, in the declaring
            # file's view, its signature respelled to the same canon the
            # concrete path declares under
            from siec.codegen.aliases import expand_alias
            from siec.codegen.functions import join_canonical_receiver

            previous, gen.current_file = gen.current_file, fn.file
            try:
                join_canonical_receiver(gen, fn)
                if fn.is_private:
                    gen.private_methods.setdefault(
                        fn.name, set()).add(fn.file)
                parameters = frozenset(fn.type_params)
                fn.return_type = expand_alias(
                    gen, fn.return_type, parameters=parameters)
                for param in fn.params:
                    param.type = expand_alias(
                        gen, param.type, parameters=parameters)
            finally:
                gen.current_file = previous

            resolve_generic_function(gen, fn)
        else:
            resolve_function(gen, fn)


def resolve_method(gen: CodeGenerator, receiver_type: str | None,
                   method: str, *, specialize: bool = True) -> str | None:
    """
    The symbol of a method on a receiver's type, stamping a generic
    struct's template on first use; None when the type has none.

    Conformance checking passes ``specialize=False`` because its resolution
    pass has already stamped every candidate it may inspect.
    """
    base = strip_const(strip_reference(receiver_type)) if receiver_type else None
    if not base:
        return None

    symbol = f"{base}::{method}"
    exact = (
        symbol in gen.generic_functions
        or symbol in gen.overloads
        or symbol in gen.resolved_functions
        or isinstance(gen.module.globals.get(symbol), ir.Function)
    )

    overrides = gen.overridden_method_signatures.get(symbol, set())

    # An ordinary concrete array specialization owns its method name, as it
    # did before explicit overrides. An '@override' instead suppresses only
    # its matching family signature below, preserving sibling overloads.
    if base.endswith("[]") and exact and not overrides:
        if not gen.sees_method(symbol):
            return None
        return symbol

    # a generic struct's method instantiates with the struct's arguments;
    # stamping comes first, so the templates join any overloads declared
    # directly on the instantiated name (through an alias, say); an
    # array receiver instantiates the 'T[]::m' templates, its element
    # standing in for the placeholder
    parts = split_generic(base)
    if parts is None and base.endswith("[]"):
        parts = ("[]", [base[:-2]])

    templates = gen.generic_methods.get((parts[0], method)) if parts else None
    template_entries = []
    if templates:
        template_entries = [
            (template, dict(zip(template.receiver_params, parts[1])))
            for template in templates
            if len(template.receiver_params or ()) == len(parts[1])
        ]
        templates = [template for template, _ in template_entries]
        if overrides:
            template_entries = [
                (template, mapping)
                for template, mapping in template_entries
                if method_signature(template, mapping) not in overrides
            ]
            templates = [template for template, _ in template_entries]
    else:
        # An inherent method on this exact type is more specific than a
        # blanket receiver family and keeps its ordinary declaration.
        if exact:
            if not gen.sees_method(symbol):
                return None
            return symbol

        # A blanket receiver template unifies its receiver placeholder
        # directly from the carried concrete type.
        from siec.codegen.generics import unify

        for template in gen.generic_receiver_methods.get(method, ()):
            mapping = {}
            unify(template.receiver, base, template.receiver_params, mapping)
            if all(param in mapping for param in template.receiver_params):
                template_entries.append((template, mapping))

        templates = [template for template, _ in template_entries]

    # A method bypasses module-qualified lookup because its receiver carries
    # the type. Keep private methods within the same textual include module.
    private_templates = [
        template for template in (templates or ()) if template.is_private
    ]
    if (private_templates
            and not any(gen.sees_private_from(template.file)
                        for template in private_templates)):
        return None

    if not templates or symbol in gen.instantiated_functions:
        if exact:
            if not gen.sees_method(symbol):
                return None
            return symbol

        return None

    if not specialize:
        return None

    # A method may repeat bounds on its generic receiver declaration.
    # Check them before stamping any overload for this instantiation.
    if any(template.receiver_constraints for template in templates):
        from siec.codegen.interfaces import check_constraints

        eligible = []
        failure = None
        for template, mapping in template_entries:
            if not template.receiver_constraints:
                eligible.append((template, mapping))
                continue

            receiver_template = copy.copy(template)
            receiver_template.constraints = template.receiver_constraints
            try:
                check_constraints(
                    gen,
                    receiver_template,
                    mapping,
                )
            except TypeError as error:
                failure = failure or error
            else:
                eligible.append((template, mapping))

        if not eligible:
            intrinsic_numeric_family = all(
                template.file == "<prelude>"
                and set((template.receiver_constraints or {}).values())
                == {"Numeric"}
                for template in templates
            )
            if intrinsic_numeric_family:
                return symbol if exact else None
            raise failure

        template_entries = eligible
        templates = [template for template, _ in eligible]

    template_entries = select_method_overrides(
        gen, base, method, template_entries)
    templates = [template for template, _ in template_entries]

    gen.instantiated_functions.add(symbol)
    site = gen.type_instantiation_sites.get(base)
    if site is None and gen.current_function is not None:
        site = (gen.current_function, gen.current_file, gen.current_line)
    if site is not None:
        gen.instantiation_sites.setdefault(symbol, site)

    # the method's overloads stamp together, joining one set under
    # the instantiated symbol for calls to pick among
    for template, mapping in template_entries:
        if template.is_private:
            gen.private_methods.setdefault(symbol, set()).add(template.file)

        instance = copy.deepcopy(template)
        instance.name = symbol
        instance.receiver = instance.receiver_params = None
        substitute_types(instance, mapping)

        # a still-generic method waits for its own arguments; a concrete
        # one declares like any instantiation - either way its
        # substituted types mix files' names, so no view gates them
        if instance.type_params is not None:
            from siec.codegen.generics import resolve_generic_function

            resolve_generic_function(gen, instance)
        else:
            from siec.codegen.worklist import resolve_function_instance

            resolved_symbol = resolve_function_instance(
                gen,
                instance,
                deferred=len(templates) != 1,
            )
            if site is not None:
                gen.instantiation_sites.setdefault(resolved_symbol, site)

    return symbol


def takes_receiver(gen: CodeGenerator, symbol: str) -> bool:
    """
    Whether a resolved method's first parameter is its receiver; a static
    method has none, and its calls pass no instance.
    """
    from siec.codegen.overloads import overload_candidates

    base = symbol.partition("::")[0]
    if (template := gen.generic_functions.get(symbol)) is not None:
        first = template.params[0].type if template.params else None
    else:
        # any candidate answers: overloads share their receiver-ness
        params = gen.param_types.get(overload_candidates(gen, symbol)[0], ())
        first = params[0] if params else None

    if first is None:
        return False

    # a plain function standing in for a method (an array's 'iterator')
    # takes the receiver as its reference first parameter
    if "::" not in symbol:
        from siec.codegen.types import is_reference

        return is_reference(strip_const(first))

    return strip_const(first) == f"&{base}"


def qualified_method(gen: CodeGenerator, name: str) -> str | None:
    """
    Resolve a written 'S::m' callee: the receiver type expands like any
    written type (aliases, visibility), the method resolves on the result.

    A name that is already a resolved symbol - one a receiver's carried
    type stamped - is its own answer, unexpanded: the receiver picked
    the method, no file's view gates it.
    """
    from siec.codegen.aliases import expand_alias

    if (name in gen.generic_functions or name in gen.overloads
            or name in gen.resolved_functions
            or isinstance(gen.module.globals.get(name), ir.Function)):
        return name

    base, _, method = name.partition("::")
    return resolve_method(gen, expand_alias(gen, base), method)


def iteration_getter(gen: CodeGenerator, source: str) -> str | None:
    """
    The method 'foreach' or 'enumerate' asks its source for: a const
    value iterates through 'const_iterator', a mutable one through
    'iterator', falling back to 'const_iterator' when that is all the
    type offers.
    """
    from siec.codegen.types import is_const

    wants = (["const_iterator"] if is_const(source)
             else ["iterator", "const_iterator"])
    return next((method for method in wants
                 if resolve_method(gen, source, method) is not None), None)


def rewrite_enumerate(gen: CodeGenerator, call: Call, scope: dict) -> Call | None:
    """
    Rewrite the builtin 'enumerate(x)' into its mutable or const helper:
    the argument's iterator type and element type spell the arguments,
    an Iterable handing out its iterator first. A const iterator keeps its
    element contract through ConstEnumerated<T>. A user declaration named
    'enumerate' takes precedence; None leaves the call untouched.
    """
    from siec.ast import MethodCall
    from siec.codegen.inference import expr_sie_type
    from siec.codegen.types import is_const, is_reference

    if call.name != "enumerate" or "enumerate" in scope:
        return None

    # a declared 'enumerate' - the user's - wins over the builtin
    symbol = gen.resolve_symbol("enumerate")
    if (symbol in gen.generic_functions or symbol in gen.overloads
            or symbol in gen.resolved_functions
            or isinstance(gen.module.globals.get(symbol), ir.Function)):
        return None

    if len(call.args) != 1 or call.type_args is not None:
        raise TypeError("'enumerate' takes one iterable value")

    arg = call.args[0]
    source = expr_sie_type(gen, arg, scope)
    source = strip_reference(source) if source else None
    if not source:
        raise TypeError("cannot enumerate: the expression has no type")

    # an Iterable hands out its iterator; an iterator enumerates itself
    if (getter := iteration_getter(gen, source)) is not None:
        arg = MethodCall(arg, getter, [], None)
        it_type = expr_sie_type(gen, arg, scope)
    elif resolve_method(gen, strip_const(source), "has_next") is not None:
        it_type = strip_const(source)
    else:
        raise TypeError(f"cannot enumerate a {source!r} value: it is "
                        "neither an Iterable nor an Iterator")

    from siec.codegen.overloads import overload_candidates

    next_ = resolve_method(gen, it_type, "next")
    next_ret = (gen.return_types.get(overload_candidates(gen, next_)[0])
                if next_ is not None else None)
    if not is_reference(next_ret):
        raise TypeError(f"cannot enumerate: type {it_type!r} has no "
                        "'next' returning a reference")

    value_type = strip_reference(next_ret)
    element = strip_const(value_type)
    helper = "__const_enumerate" if is_const(value_type) else "__enumerate"

    # the arguments are carried canonical names; no view gates them
    from siec.codegen.generics import instantiate_function

    gen.ungated_types += 1
    try:
        symbol = instantiate_function(gen, gen.generic_functions[helper],
                                      [it_type, element])
    finally:
        gen.ungated_types -= 1

    from siec.codegen.ownership import inherit_expression_identity

    return inherit_expression_identity(call, Call(symbol, [arg]))


def method_reference(gen: CodeGenerator, expr) -> ir.Function | None:
    """
    The function a bare 'S::m' spelling references, when S names a type
    with that method; the value calls like any function reference, an
    instance method taking its receiver as an ordinary '&S' argument.
    """
    try:
        symbol = qualified_method(gen, f"{expr.enum}::{expr.member}")
    except (NameError, TypeError):
        return None

    # an overloaded method has no arguments to pick its candidate by
    if symbol is not None and len(gen.overloads.get(symbol, ())) > 1:
        raise TypeError(f"ambiguous reference to overloaded method "
                        f"'{expr.enum}::{expr.member}'")

    if symbol is None:
        return None

    from siec.codegen.deprecation import note_use
    from siec.codegen.overloads import overload_candidates

    candidate = overload_candidates(gen, symbol)[0]
    func = gen.module.globals.get(candidate)
    if not isinstance(func, ir.Function):
        return None

    # handing the method around reaches it as surely as calling it
    note_use(gen, candidate)
    return func


def method_reference_type(gen: CodeGenerator, expr) -> str | None:
    """
    The 'fn(...)' type a bare 'S::m' method reference carries; None when
    the spelling references no concrete method.
    """
    try:
        symbol = qualified_method(gen, f"{expr.enum}::{expr.member}")
    except (NameError, TypeError):
        return None

    if symbol is None:
        return None

    from siec.codegen.overloads import overload_candidates

    symbol = overload_candidates(gen, symbol)[0]
    if symbol not in gen.param_types:
        return None

    params = ",".join(gen.param_types[symbol])
    ret = gen.return_types.get(symbol)
    return f"fn({params})" + (f"->{ret}" if ret else "")


def method_call(gen: CodeGenerator, call: Call, scope: dict) -> tuple | None:
    """
    Interpret a dotted call as a method on its receiver chain:
    's.method()' or 'self.field.method()'. Returns the method's symbol
    and the receiver expression, or None when the chain types to no
    struct or its type has no such method.
    """
    from siec.codegen.inference import expr_sie_type

    names = call.name.split(".")
    receiver = Var(names[0])
    if hasattr(call, "macro_argument_file"):
        receiver.macro_argument_file = call.macro_argument_file
    for part in names[1:-1]:
        receiver = Member(receiver, part)
        if hasattr(call, "macro_argument_file"):
            receiver.macro_argument_file = call.macro_argument_file

    from siec.codegen.resolution import expression_view

    with expression_view(gen, call):
        receiver_type = expr_sie_type(gen, receiver, scope)
        if receiver_type is None:
            return None

        symbol = resolve_method(gen, receiver_type, names[-1])
    if symbol is None:
        return None

    # a static reached through an instance takes no receiver argument
    return symbol, (receiver if takes_receiver(gen, symbol) else None)


def emit_method_call(gen: CodeGenerator, builder, expr, scope: dict,
                     as_address: bool = False):
    """
    Emit a method call on a receiver expression: the receiver's type
    picks the method, and joins the arguments as the hidden first one.
    """
    from siec.codegen.calls import emit_call
    from siec.codegen.hir import resolved_callee, stamp
    from siec.codegen.inference import expr_sie_type

    # Prefer the callee checking already selected.
    symbol = resolved_callee(expr)
    if symbol is None:
        receiver_type = expr_sie_type(gen, expr.receiver, scope)
        symbol = resolve_method(gen, receiver_type, expr.method)
    if symbol is None:
        from siec.codegen.deprecation import check_removed_method

        receiver_type = expr_sie_type(gen, expr.receiver, scope)
        # a removed method leaves no declaration to resolve; its name
        # still answers for the advice
        check_removed_method(gen, receiver_type, expr.method)
        raise TypeError(f"type {receiver_type or '?'} has no method "
                        f"{expr.method!r}")

    # a static's receiver expression evaluates only for its effects
    if not takes_receiver(gen, symbol):
        from siec.codegen.expressions import emit_expression

        emit_expression(gen, builder, expr.receiver, None, scope)
        call = Call(symbol, list(expr.args), expr.type_args)
    else:
        call = Call(symbol, [expr.receiver, *expr.args], expr.type_args)

    # Carry HIR stamps onto the synthetic call so emit_call skips re-pick.
    stamp(call, resolved_symbol=symbol, overwrite=True)
    if (context := getattr(expr, "expected_type", None)) is not None:
        call.expected_type = context
    if (sie_type := getattr(expr, "sie_type", None)) is not None:
        stamp(call, sie_type=sie_type, overwrite=True)

    return emit_call(gen, builder, call, scope, as_address)


def constructor_type(gen: CodeGenerator, call, symbol: str | None) -> str | None:
    """
    The struct type a 'S(...)' call constructs - through aliases and
    generic arguments alike; None when the name isn't a type's.
    """
    from siec.codegen.aliases import expand_alias

    if not symbol:
        return None

    name = symbol
    if call.type_args is not None:
        name += f"<{','.join(call.type_args)}>"

    base = name.partition("<")[0]
    if not (base in gen.structs or base in gen.generic_structs
            or base in gen.aliases or base in gen.generic_aliases):
        return None

    if base in gen.generic_structs and "<" not in name:
        raise TypeError(f"generic struct {base!r} needs its type arguments "
                        f"to construct: '{base}<...>()'")

    # ``symbol`` has already passed plain or qualified declaration lookup.
    # Do not gate its canonical spelling a second time as though the caller
    # had written that unqualified name.
    canonical = expand_alias(gen, name, checked=False)
    return canonical if strip_const(canonical) in gen.structs else None


def emit_constructor(gen: CodeGenerator, builder, type_name: str, call,
                     scope: dict, as_address: bool = False):
    """
    Emit 'S(args)': stack space for an instance, its field defaults, then
    'S::init(self, args...)' - the expression form of
    'let s: S; s.init(args...);', yielding the instance.
    """
    from siec.codegen.calls import emit_argument
    from siec.codegen.expressions import default_value
    from siec.codegen.generator import entry_alloca
    from siec.codegen.types import resolve_type

    llvm_type = resolve_type(type_name, gen.structs)
    slot = entry_alloca(builder, llvm_type, "ctor")
    if (align := gen.struct_align(type_name)) is not None:
        slot.align = align

    # the instance starts like a bare declaration: from its defaults
    if (default := default_value(gen, builder, type_name)) is not None:
        builder.store(default, slot)

    symbol = resolve_method(gen, type_name, "init")
    if symbol is None:
        raise TypeError(f"type {type_name!r} has no 'init' method to "
                        "construct it")

    if not takes_receiver(gen, symbol):
        raise TypeError(f"a static 'init' cannot construct {type_name!r}: "
                        "the constructor passes the instance as its receiver")

    # an overloaded 'init' resolves to the candidate the arguments pick,
    # the instance's type standing in for the receiver they lack; a call
    # no concrete candidate takes falls through to a generic template
    picked = False
    if symbol in gen.overloads:
        from siec.codegen.overloads import pick_overload

        try:
            symbol = pick_overload(gen, symbol, call.args, scope,
                                   receiver=type_name)
            picked = True
        except TypeError:
            if gen.generic_functions.get(symbol) is None:
                raise

    # a stamped overload's body waits for its first picked call
    from siec.codegen.worklist import activate_function_instance

    activate_function_instance(gen, symbol)

    # a generic 'init' (one taking an interface parameter, say)
    # instantiates like any generic call, the fresh instance joining
    # through a hidden scope name as its receiver
    if not picked and symbol in gen.generic_functions:
        from siec.codegen.calls import emit_call
        from siec.codegen.generator import Variable

        inner = dict(scope)
        inner[".ctor.self"] = Variable(slot, type_name)
        emit_call(gen, builder,
                  Call(f"{type_name}::init", [Var(".ctor.self"), *call.args]),
                  inner)

        return slot if as_address else builder.load(slot)

    func = gen.module.globals[symbol]
    sie_params = gen.param_types[func.name]
    expected = len(func.function_type.args) - 1

    # trailing parameters with defaults are optional here too
    defaults, defaults_file = gen.param_defaults.get(func.name, ([], None))
    required = expected
    while (required and required + 1 <= len(defaults)
           and defaults[required] is not None):
        required -= 1

    if len(call.args) < required:
        raise TypeError(f"too few arguments to function {symbol!r}")

    if len(call.args) > expected:
        raise TypeError(f"too many arguments to function {symbol!r}")

    args = [slot]
    for i, arg in enumerate(call.args):
        args.append(emit_argument(gen, builder, arg, sie_params[i + 1], scope))

    # omitted arguments take init's declared defaults, emitted under the
    # declaring file's view, away from any local names
    if len(call.args) < expected:
        previous, gen.current_file = gen.current_file, defaults_file
        try:
            for i in range(len(call.args), expected):
                args.append(emit_argument(gen, builder, defaults[i + 1],
                                          sie_params[i + 1], {}))
        finally:
            gen.current_file = previous

    builder.call(func, args)
    return slot if as_address else builder.load(slot)
