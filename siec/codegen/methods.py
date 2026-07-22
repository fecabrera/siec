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
from siec.codegen.generics import split_generic, substitute_types
from siec.codegen.types import strip_const, strip_reference


def register_method(gen: CodeGenerator, fn) -> None:
    """
    Register a method: a plain one declares like any function under its
    'S::m' name, a generic one like a generic function, and a generic
    struct's becomes a template stamped per struct instantiation.
    """
    from siec.codegen.functions import declare_function
    from siec.codegen.generics import register_generic_function
    from siec.codegen.overloads import overload_key

    with source_location(line=fn.line, file=fn.file):
        if fn.receiver_params is not None:
            if fn.body is None and fn.asm is None:
                raise TypeError(f"method {fn.name!r} needs a body: there is "
                                "nothing to declare without one")

            # a generic struct's method may overload like any other, its
            # templates stamped together per struct instantiation; only
            # one may take type parameters of its own (an interface
            # parameter's synthetic ones included), since a generic
            # template registers whole, with no set to pick among
            key = (fn.receiver, fn.name.partition("::")[2])
            templates = gen.generic_methods.setdefault(key, [])

            if (fn.type_params is not None
                    and any(t.type_params is not None for t in templates)):
                raise TypeError(f"cannot overload method {fn.name!r} with "
                                "more than one generic signature")

            if any(overload_key(t.params) == overload_key(fn.params)
                   for t in templates):
                raise TypeError(f"method {fn.name!r} is declared more than once")

            templates.append(fn)
        elif fn.type_params is not None:
            register_generic_function(gen, fn)
        else:
            declare_function(gen, fn)


def resolve_method(gen: CodeGenerator, receiver_type: str | None,
                   method: str) -> str | None:
    """
    The symbol of a method on a receiver's type, stamping a generic
    struct's template on first use; None when the type has none.
    """
    base = strip_const(strip_reference(receiver_type)) if receiver_type else None
    if not base:
        return None

    # a 'T[]' array answers 'iterator()' itself, through the builtin
    # ArrayIterator<T>: that is how arrays implement Iterable<T>; a
    # 'const T[]' iterates through ConstArrayIterator<T>, its elements
    # referenced 'const &T'
    if base.endswith("[]") and method == "iterator":
        from siec.codegen.generics import instantiate_function
        from siec.codegen.types import is_const

        helper = ("__const_array_iterator"
                  if is_const(strip_reference(receiver_type))
                  else "__array_iterator")
        template = gen.generic_functions.get(helper)
        if template is not None:
            # the element is a carried canonical name; no view gates it
            gen.ungated_types += 1
            try:
                return instantiate_function(gen, template, [base[:-2]])
            finally:
                gen.ungated_types -= 1

    symbol = f"{base}::{method}"

    # a generic struct's method instantiates with the struct's arguments;
    # stamping comes first, so the templates join any overloads declared
    # directly on the instantiated name (through an alias, say)
    parts = split_generic(base)
    templates = gen.generic_methods.get((parts[0], method)) if parts else None

    if not templates or symbol in gen.instantiated_functions:
        if (symbol in gen.generic_functions or symbol in gen.overloads
                or isinstance(gen.module.globals.get(symbol), ir.Function)):
            return symbol

        return None

    struct_base, args = parts
    gen.instantiated_functions.add(symbol)

    # the method's overloads stamp together, joining one set under
    # the instantiated symbol for calls to pick among
    for template in templates:
        instance = copy.deepcopy(template)
        instance.name = symbol
        instance.receiver = instance.receiver_params = None
        substitute_types(instance, dict(zip(template.receiver_params, args)))

        # a still-generic method waits for its own arguments; a concrete
        # one declares like any instantiation - either way its
        # substituted types mix files' names, so no view gates them
        if instance.type_params is not None:
            gen.generic_functions[symbol] = instance
        else:
            from siec.codegen.functions import declare_function

            gen.ungated_types += 1
            try:
                func = declare_function(gen, instance)
            finally:
                gen.ungated_types -= 1

            # a lone signature's body queues at once; overloads wait for
            # a call to pick them, so a candidate fitting only some
            # element types never emits unpicked
            if len(templates) == 1:
                gen.pending_functions.append(instance)
            else:
                gen.deferred_overloads[func.name] = instance

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
            or isinstance(gen.module.globals.get(name), ir.Function)):
        return name

    base, _, method = name.partition("::")
    return resolve_method(gen, expand_alias(gen, base), method)


def rewrite_enumerate(gen: CodeGenerator, call: Call, scope: dict) -> Call | None:
    """
    Rewrite the builtin 'enumerate(x)' into its '__enumerate<I, T>' call:
    the argument's iterator type and element type spell the arguments,
    an Iterable handing out its iterator first. A user declaration named
    'enumerate' takes precedence; None leaves the call untouched.
    """
    from siec.ast import MethodCall
    from siec.codegen.inference import expr_sie_type
    from siec.codegen.types import is_reference

    if call.name != "enumerate" or "enumerate" in scope:
        return None

    # a declared 'enumerate' - the user's - wins over the builtin
    symbol = gen.resolve_symbol("enumerate")
    if (symbol in gen.generic_functions or symbol in gen.overloads
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
    if resolve_method(gen, source, "iterator") is not None:
        arg = MethodCall(arg, "iterator", [], None)
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

    element = strip_const(strip_reference(next_ret))

    # the arguments are carried canonical names; no view gates them
    from siec.codegen.generics import instantiate_function

    gen.ungated_types += 1
    try:
        symbol = instantiate_function(gen, gen.generic_functions["__enumerate"],
                                      [it_type, element])
    finally:
        gen.ungated_types -= 1

    return Call(symbol, [arg])


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

    from siec.codegen.overloads import overload_candidates

    func = gen.module.globals.get(overload_candidates(gen, symbol)[0])
    return func if isinstance(func, ir.Function) else None


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
    for part in names[1:-1]:
        receiver = Member(receiver, part)

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
    from siec.codegen.inference import expr_sie_type

    receiver_type = expr_sie_type(gen, expr.receiver, scope)
    symbol = resolve_method(gen, receiver_type, expr.method)
    if symbol is None:
        raise TypeError(f"type {receiver_type or '?'} has no method "
                        f"{expr.method!r}")

    # a static's receiver expression evaluates only for its effects
    if not takes_receiver(gen, symbol):
        from siec.codegen.expressions import emit_expression

        emit_expression(gen, builder, expr.receiver, None, scope)
        call = Call(symbol, list(expr.args), expr.type_args)
    else:
        call = Call(symbol, [expr.receiver, *expr.args], expr.type_args)

    # a coercion target rides along to drive a generic method's arguments
    if (context := getattr(expr, "expected_type", None)) is not None:
        call.expected_type = context

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

    canonical = expand_alias(gen, name)
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
    if (instance := gen.deferred_overloads.pop(symbol, None)) is not None:
        gen.pending_functions.append(instance)

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
