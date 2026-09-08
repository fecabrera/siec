"""Require annotated results to be stored, used, or forwarded explicitly."""

from dataclasses import fields, is_dataclass

from siec import ast
from siec.codegen.errors import source_location


def children(node):
    """Visit source fields only, without following links in checked plans."""
    if is_dataclass(node):
        for field in fields(node):
            value = getattr(node, field.name)
            if isinstance(value, list):
                yield from value
            elif is_dataclass(value):
                yield value


def replacement(expr):
    """Follow the expression selected by semantic checking."""
    for name in ('call_plan', 'binary_plan'):
        value = getattr(getattr(expr, name, None), 'replacement', None)
        if value is not None:
            return value
    for name in ('expansion', 'macro_call', 'qualified_value',
                 'item_get_call', 'slice_call'):
        value = getattr(expr, name, None)
        if value is not None:
            return value
    return None


def pattern_names(pattern):
    """Read names from a possibly nested tuple pattern."""
    for name in pattern:
        if isinstance(name, list):
            yield from pattern_names(name)
        else:
            yield name


class RequiredResults:
    """Keep callable contracts separate from the values their calls return.

    Sets contain possible function targets, never required-result flags for
    ordinary variables. Merging possible targets is conservative: overwriting
    a callback does not erase an earlier contract on the same storage.
    """

    def __init__(self, gen, functions):
        self.gen = gen
        self.functions = dict(functions)
        self.values = {}
        self.equations = []
        self.calls = []
        self.obligations = []
        self.visited = set()

    def add(self, key, values):
        """Add possible targets and report whether the graph changed."""
        stored = self.values.setdefault(key, set())
        before = len(stored)
        stored.update(values)
        return len(stored) != before

    def reference(self, expr, env):
        """Find a function reference or the storage holding a callable."""
        plan = getattr(expr, 'coercion_plan', None)
        if plan and plan.kind == 'function_reference':
            return {plan.symbol}
        resolved = getattr(expr, 'resolved_symbol', None)
        if resolved in self.gen.return_types:
            return {resolved}
        name = (f'{expr.enum}::{expr.member}' if isinstance(expr, ast.EnumMember)
                else getattr(expr, 'name', ''))
        if name in env:
            return self.values.get(env[name], set())
        if '.' in name and name.split('.')[0] in env:
            return self.values.get(env[name.split('.')[0]], set())
        file = (getattr(expr, 'module_file', None)
                or getattr(expr, 'macro_argument_file', None) or env.get(None))
        with self.gen.in_file(file):
            symbol = self.gen.resolve_symbol(name)
        if symbol in self.gen.globals:
            return self.values.get(('global', symbol), set())
        if symbol in self.gen.return_types:
            return {symbol}
        candidates = self.gen.overloads.get(symbol, ())
        return {candidates[0][1]} if len(candidates) == 1 else set()

    def targets(self, expr, env):
        """Read resolved direct targets or possible indirect targets."""
        plan = getattr(expr, 'call_plan', None)
        if not plan:
            return set()
        if plan.kind == 'indirect':
            if plan.indirect_symbol:
                return self.values.get(('global', plan.indirect_symbol), set())
            return self.reference(expr, env)
        return {plan.symbol} if plan.symbol else set()

    def sources(self, expr, env):
        """Find callable values carried by an expression, not call obligations."""
        if expr is None:
            return set()
        rewritten = replacement(expr)
        if rewritten is not None:
            return self.sources(rewritten, env)
        if isinstance(expr, ast.ClosureExpr):
            return {('closure', id(expr))}
        if isinstance(expr, (ast.Var, ast.EnumMember)):
            return self.reference(expr, env)
        if isinstance(expr, (ast.Call, ast.MethodCall)):
            result = set()
            for target in self.targets(expr, env):
                result.update(self.values.get(('return', target), set()))
            if getattr(getattr(expr, 'call_plan', None), 'kind', None) == 'constructor':
                for arg in expr.args:
                    result.update(self.sources(arg, env))
            return result
        if isinstance(expr, ast.Ternary):
            return self.sources(expr.then, env) | self.sources(expr.orelse, env)
        if isinstance(expr, ast.Member):
            return self.sources(expr.base, env)
        if isinstance(expr, ast.BlockExpr):
            return self.values.get(('emit', id(expr)), set())
        result = set()
        for child in children(expr):
            result.update(self.sources(child, env))
        return result

    def unit(self, symbol, fn, captured=None):
        """Register a function or closure with its own lexical bindings."""
        if symbol in self.visited:
            return
        self.visited.add(symbol)
        env = dict(captured or {})
        # None cannot be a source name; it carries the scope's file view.
        env[None] = fn.file
        with self.gen.in_file(fn.file):
            for declaration in self.gen.program.globals:
                resolved = self.gen.resolve_symbol(declaration.name)
                if resolved in self.gen.globals:
                    env.setdefault(declaration.name, ('global', resolved))
        for param in fn.params:
            env[param.name] = ('param', symbol, param.name)
            if param.pattern:
                for name in pattern_names(param.pattern):
                    env[name] = env[param.name]
        self.block(fn.body or [], env, symbol, fn)

    def expression(self, expr, env, symbol, fn):
        """Find nested calls and blocks, including calls inside stored results."""
        rewritten = replacement(expr)
        if rewritten is not None:
            self.expression(rewritten, env, symbol, fn)
            return
        if isinstance(expr, ast.ClosureExpr):
            key = ('closure', id(expr))
            self.functions[key] = expr
            self.unit(key, expr, env)
            return
        if isinstance(expr, (ast.BlockExpr, ast.Block)):
            self.block(expr.body, dict(env), symbol, fn, ('emit', id(expr)))
            return
        if isinstance(expr, ast.Try):
            self.expression(expr.result, env, symbol, fn)
            self.block(expr.body or [], dict(env), symbol, fn, ('emit', id(expr)))
            return
        if isinstance(expr, (ast.Call, ast.MethodCall)) and getattr(expr, 'call_plan', None):
            self.calls.append((expr, dict(env)))
        for child in children(expr):
            self.expression(child, env, symbol, fn)

    def block(self, body, env, symbol, fn, emitted=None):
        """Record uses at statement boundaries without tracking stored results."""
        from siec.codegen.checking import assignment_target

        for stmt in body:
            context = (getattr(stmt, 'line', 0), fn.file)
            if isinstance(stmt, (ast.Let, ast.LocalFunction)):
                self.expression(stmt.value, env, symbol, fn)
                key = ('local', id(stmt), stmt.name)
                self.equations.append((key, stmt.value, dict(env)))
                env[stmt.name] = key
            elif isinstance(stmt, ast.LetTuple):
                self.expression(stmt.value, env, symbol, fn)
                original = dict(env)
                for name in pattern_names(stmt.pattern):
                    key = ('local', id(stmt), name)
                    self.equations.append((key, stmt.value, original))
                    env[name] = key
            elif isinstance(stmt, (ast.Assign, ast.MemberAssign,
                                   ast.RefAssign, ast.IndexAssign)):
                target = assignment_target(stmt)
                self.expression(target, env, symbol, fn)
                while isinstance(target, (ast.Member, ast.Index)):
                    target = target.base
                if isinstance(target, ast.Var):
                    key = env.get(target.name, ('global', target.name))
                    self.equations.append((key, stmt.value, dict(env)))
                self.expression(stmt.value, env, symbol, fn)
            elif isinstance(stmt, (ast.Return, ast.Emit)):
                self.expression(stmt.value, env, symbol, fn)
                if isinstance(stmt, ast.Return):
                    self.obligations.append((stmt.value, dict(env), symbol,
                                             'return', context))
                    key = ('return', symbol)
                else:
                    key = emitted
                if key is not None:
                    self.equations.append((key, stmt.value, dict(env)))
            elif isinstance(stmt, ast.ExprStmt):
                self.expression(stmt.expr, env, symbol, fn)
                self.obligations.append((stmt.expr, dict(env), symbol,
                                         'discard', context))
            elif isinstance(stmt, ast.For):
                inner = dict(env)
                self.block([stmt.init], inner, symbol, fn, emitted)
                self.expression(stmt.condition, inner, symbol, fn)
                self.block(stmt.body, dict(inner), symbol, fn, emitted)
                self.block([stmt.step], inner, symbol, fn, emitted)
            else:
                # Conditions consume their values. Their nested statements
                # still need checks, as do deferred statements and loop bodies.
                for field in fields(stmt) if is_dataclass(stmt) else ():
                    value = getattr(stmt, field.name)
                    if isinstance(value, list):
                        self.block(value, dict(env), symbol, fn, emitted)
                    elif field.name == 'stmt' and is_dataclass(value):
                        self.block([value], dict(env), symbol, fn, emitted)
                    elif is_dataclass(value):
                        self.expression(value, env, symbol, fn)

    def required(self, expr, env):
        """Return annotated calls whose values still reach this expression."""
        if expr is None:
            return set()
        rewritten = replacement(expr)
        if rewritten is not None:
            return self.required(rewritten, env)
        if isinstance(expr, (ast.Var, ast.ClosureExpr)):
            return set()
        if isinstance(expr, (ast.Call, ast.MethodCall)):
            # Passing a value to a function uses that value. Only the called
            # function's own return contract applies to the surrounding use.
            return self.targets(expr, env) & self.gen.nodiscard
        if isinstance(expr, ast.Ternary):
            return self.required(expr.then, env) | self.required(expr.orelse, env)
        if isinstance(expr, (ast.BlockExpr, ast.Block, ast.Try)):
            # A try uses the Result by checking its error. Only an emitted
            # fallback can introduce a new required result.
            result = set()
            for key, value, scope in self.equations:
                if key == ('emit', id(expr)):
                    result.update(self.required(value, scope))
            return result
        result = set()
        for child in children(expr):
            result.update(self.required(child, env))
        return result

    def run(self):
        """Solve callable contracts before checking any discarded results."""
        for declaration in self.gen.program.globals:
            with self.gen.in_file(declaration.file):
                symbol = self.gen.resolve_symbol(declaration.name)
            self.equations.append((('global', symbol), declaration.value,
                                   {None: declaration.file}))
        for symbol, fn in list(self.functions.items()):
            self.unit(symbol, fn)
        changed = True
        while changed:
            changed = False
            for key, expr, env in self.equations:
                changed |= self.add(key, self.sources(expr, env))
            for expr, env in self.calls:
                args = list(expr.args)
                plan = expr.call_plan
                if plan.passes_receiver:
                    args.insert(0, getattr(expr, 'receiver', None) or plan.receiver)
                for target in self.targets(expr, env):
                    called = self.functions.get(target)
                    if called is not None:
                        defaults, file = self.gen.param_defaults.get(target, ([], None))
                        actual = args + defaults[len(args):len(called.params)]
                        for index, (param, arg) in enumerate(zip(called.params, actual)):
                            changed |= self.add(('param', target, param.name),
                                                self.sources(arg, env if index < len(args)
                                                             else {None: file}))
        from siec.codegen.overloads import display_name

        for expr, env, symbol, mode, (line, file) in self.obligations:
            required = self.required(expr, env)
            if not required or (mode == 'return' and symbol in self.gen.nodiscard):
                continue
            name = display_name(sorted(required)[0])
            with source_location(line=line, file=file):
                if mode == 'discard':
                    raise TypeError(f"result of @nodiscard function {name!r} "
                                    "is not used")
                caller = self.functions[symbol].name or '<closure>'
                raise TypeError(f"function {caller!r} must declare @nodiscard "
                                f"to forward the result of {name!r}")


def check_nodiscard(gen, functions):
    """Check completed function bodies before LLVM lowering."""
    if gen.nodiscard:
        RequiredResults(gen, functions).run()
