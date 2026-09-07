"""Local initialization, ownership, and reference-return checks.

This pass consumes resolved expressions. It does not instantiate methods or
build LLVM instructions. Loop states reach a fixed point before the exit state
is used. Raw pointer access and Slot payload state retain their caller contracts.
"""

from dataclasses import dataclass, fields, is_dataclass, replace

from siec import ast
from siec.codegen.errors import source_location
from siec.codegen.generator import Variable
from siec.codegen.types import (is_const, is_reference, strip_const,
                                strip_reference, sized_array, fn_type_parts, raw_array)


@dataclass(frozen=True)
class LocalState:
    """Initialization and move facts for one local binding."""

    type: str
    definite: frozenset = frozenset()
    possible: frozenset = frozenset()
    moved: bool = False
    origin: frozenset = frozenset()
    storage: frozenset = frozenset({'local'})
    binding: object = None


def merge_states(paths):
    """Intersect definite writes and union possible writes and moves."""
    if not paths:
        return None
    result = dict(paths[0])
    for name, first in list(result.items()):
        values = [path[name] for path in paths if name in path]
        result[name] = replace(
            first,
            definite=frozenset.intersection(*(v.definite for v in values)),
            possible=frozenset.union(*(v.possible for v in values)),
            moved=any(v.moved for v in values),
            origin=frozenset.union(*(v.origin for v in values)))
    return result


class LocalFlow:
    """Check local facts on each continuing, break, and continue path."""

    def __init__(self, gen, fn, params):
        self.gen, self.fn = gen, fn
        self.params = params
        self.loops = []
        self.emits = []
        self.defers = []
        self.required_cache = {}
        self.initializer = (fn.name.partition('::')[2].split('(')[0] == 'init'
                            and bool(fn.params) and is_reference(fn.params[0].type))
        self.in_defer = False

    def required(self, type_name):
        """List initialized field paths needed to read a whole value."""
        name = strip_const(strip_reference(type_name))
        if name in self.required_cache:
            return self.required_cache[name]
        self.required_cache[name] = frozenset({()})
        info = self.gen.structs.get(name)
        raw = raw_array(name)
        if raw is not None and not raw[2]:
            from siec.codegen.enums import evaluate_size

            size = evaluate_size(self.gen, raw[1])
            # Avoid expanding arbitrarily large raw layouts during Check.
            # Such values can still be initialized by a whole-value store.
            result = (frozenset((str(index),) + path
                                for index in range(size)
                                for path in self.required(raw[0]))
                      if size * len(self.required(raw[0])) <= 4096
                      else frozenset({('*',)}))
            self.required_cache[name] = result
            return result
        # Raw slots and tagged payloads have separate live-state contracts.
        if name.startswith('Slot<'):
            result = frozenset({()})
        elif name.startswith('Option<'):
            result = frozenset({('present',)})
        elif name.startswith('Result<'):
            result = frozenset({('ok',)})
        elif info is not None and info.fields is not None and not info.is_union:
            result = frozenset((field.name,) + path
                               for field in info.fields
                               for path in self.required(field.type))
        else:
            result = frozenset({()})
        self.required_cache[name] = result
        return result

    def defaults(self, type_name):
        """Find fields initialized by declaration defaults or raw storage."""
        name = strip_const(strip_reference(type_name))
        if name.startswith('Slot<'):
            return self.required(name)
        if sized_array(name):
            return self.required(name)
        from siec.codegen.checking import type_has_defaults

        if type_has_defaults(self.gen, name):
            # Emission zeroes the remaining fields when applying defaults.
            return self.required(name)
        info = self.gen.structs.get(name)
        if info is None or info.fields is None or info.is_union:
            return frozenset()
        result = set()
        for field in info.fields:
            paths = (self.required(field.type) if field.default is not None
                     else self.defaults(field.type))
            result.update((field.name,) + path for path in paths)
        return frozenset(result)

    def place(self, expr, state=None):
        """Identify direct local storage, without following raw pointers."""
        if isinstance(expr, ast.Var):
            return expr.name, ()
        if (isinstance(expr, ast.UnaryOp) and expr.op == '*'
                and isinstance(expr.operand, ast.UnaryOp)
                and expr.operand.op == '&'):
            return self.place(expr.operand.operand, state)
        if isinstance(expr, ast.Member):
            base = self.place(expr.base, state)
            if base:
                return base[0], base[1] + (expr.field,)
        if isinstance(expr, ast.Index) and state is not None:
            base = self.place(expr.base, state)
            if base:
                typ = strip_const(self.expression_type(expr.base, state) or '')
                raw = raw_array(typ)
                if typ.startswith('Tuple<') or raw and not raw[2]:
                    from siec.codegen.enums import evaluate

                    try:
                        index = evaluate(self.gen, expr.index)
                    except (TypeError, NameError):
                        return None
                    return base[0], base[1] + (str(index),)
        return None

    def expression_type(self, expr, state):
        """Read the type of an already checked expression."""
        from siec.codegen.inference import expr_sie_type

        scope = {name: Variable(None, value.type) for name, value in state.items()}
        return expr_sie_type(self.gen, expr, scope)

    def describe_storage(self, name, type_name, path):
        """Name missing storage without exposing internal anonymous-field names."""
        shown = name
        anonymous_union = False
        for part in path:
            canonical = strip_const(strip_reference(type_name))
            raw = raw_array(canonical)
            if raw is not None and not raw[2]:
                shown += f'[{part}]'
                type_name = raw[0]
                continue
            info = self.gen.structs.get(canonical)
            field = next((field for field in (info.fields or [])
                          if field.name == part), None) if info else None
            if field is None:
                shown += f'.{part}'
                continue
            type_name = field.type
            if part.startswith('#'):
                nested = self.gen.structs.get(strip_const(type_name))
                anonymous_union = bool(nested and nested.is_union)
            elif canonical.startswith('Tuple<'):
                shown += f'[{part}]'
            else:
                shown += f'.{part}'
        info = self.gen.structs.get(strip_const(strip_reference(type_name)))
        if info is not None and info.is_union:
            members = ', '.join(
                field.name if not field.name.startswith('#') else 'anonymous member'
                for field in (info.fields or []))
            kind = 'anonymous union in' if anonymous_union else 'union'
            return f'{kind} {shown!r} (members: {members})'
        return repr(shown)

    def read(self, expr, state):
        """Require the selected local value or field to be initialized."""
        place = self.place(expr, state)
        if not place or place[0] not in state:
            return
        name, prefix = place
        value = state[name]
        if value.moved:
            raise TypeError(f"use of moved value {name!r}")
        needed = {path for path in self.required(value.type)
                  if path[:len(prefix)] == prefix
                  or prefix[:len(path)] == path
                  or path[-1:] == ('*',) and prefix[:len(path) - 1] == path[:-1]}
        if not needed <= value.definite:
            shown = '.'.join((name,) + prefix)
            message = f"read of possibly uninitialized value {shown!r}"
            missing = sorted(needed - value.definite)
            descriptions = [self.describe_storage(name, value.type, path)
                            for path in missing[:4]]
            if descriptions != [repr(shown)]:
                detail = '; '.join(descriptions)
                if len(missing) > 4:
                    detail += f'; {len(missing) - 4} more'
                message += f'; not initialized on every path: {detail}'
            raise TypeError(message)

    def write(self, expr, state, origin=frozenset()):
        """Record a local write without treating its destination as a read."""
        place = self.place(expr, state)
        if not place or place[0] not in state:
            return
        name, prefix = place
        value = state[name]
        paths = frozenset(path for path in self.required(value.type)
                          if path[:len(prefix)] == prefix
                          or prefix[:len(path)] == path)
        if not prefix and is_const(value.type) and value.possible:
            raise TypeError(f"cannot initialize const variable {name!r} twice")
        state[name] = replace(value, definite=value.definite | paths,
                              possible=value.possible | paths,
                              moved=False if not prefix else value.moved,
                              origin=origin if not prefix else value.origin)

    def origins(self, expr, state, *, backing=False):
        """Trace a returned place to the first reference parameter or static storage."""
        expansion = self.expansion(expr)
        if expansion is not None:
            return self.origins(expansion, state, backing=backing)
        if isinstance(expr, ast.Var):
            if expr.name in state:
                value = state[expr.name]
                if is_reference(value.type) and not backing:
                    return value.storage
                if backing:
                    return value.origin or frozenset({'local'})
                return frozenset({'local'})
            if self.gen.resolve_symbol(expr.name) in self.gen.globals:
                return frozenset({'static'})
            return frozenset({'unknown'})
        if isinstance(expr, ast.Member):
            return self.origins(expr.base, state, backing=backing)
        if isinstance(expr, ast.Index):
            return self.origins(expr.base, state, backing=True)
        if isinstance(expr, ast.Cast):
            return self.origins(expr.operand, state, backing=backing)
        if isinstance(expr, ast.UnaryOp):
            return self.origins(expr.operand, state, backing=expr.op == '*')
        if isinstance(expr, (ast.Call, ast.MethodCall)):
            plan = getattr(expr, 'call_plan', None)
            if plan and plan.replacement is not None:
                return self.origins(plan.replacement, state)
            if plan:
                ret = (fn_type_parts(plan.indirect_type)[1]
                       if plan.kind == 'indirect'
                       else self.gen.return_types.get(plan.symbol))
                if is_reference(ret):
                    first = (plan.receiver if plan.passes_receiver
                             else expr.args[0] if expr.args else None)
                    if first is not None:
                        return self.origins(first, state)
            return frozenset({'temporary'})
        return frozenset({'unknown'})

    def check_return(self, expr, state):
        """Reject expired or unrelated reference sources and const removal."""
        if not is_reference(self.fn.return_type):
            return
        origin = self.origins(expr, state)
        if not origin <= {'first', 'static'}:
            raise TypeError('reference return must derive from the first '
                            'reference parameter or static storage')
        from siec.codegen.lvalues import reject_const_base
        if not is_const(strip_reference(self.fn.return_type)):
            scope = {name: Variable(None, value.type)
                     for name, value in state.items()}
            reject_const_base(self.gen, scope, expr)

    def expansion(self, expr):
        """Return a macro expansion already selected by expression checking."""
        call = getattr(expr, 'macro_call', expr)
        return getattr(call, 'expansion', None)

    def expression(self, expr, state, *, address=False):
        """Apply expression reads and recorded ownership transfers in order."""
        if expr is None:
            return
        expansion = self.expansion(expr)
        if expansion is not None:
            if isinstance(expansion, ast.Block):
                end = self.block(expansion.body, dict(state))
                if end is not None:
                    state.update(end)
            else:
                self.expression(expansion, state, address=address)
            return
        if isinstance(expr, ast.ClosureExpr):
            self.closure(expr, state)
            return
        if isinstance(expr, (ast.SizeOf, ast.TypeId, ast.TypeName)):
            if isinstance(expr, ast.TypeName):
                operand = ast.Var(expr.name) if isinstance(expr.name, str) else expr.name
                if strip_const(self.expression_type(operand, state) or '') == 'Any':
                    self.expression(ast.Member(operand, 'id'), state)
            return
        if isinstance(expr, ast.TypeOf):
            if strip_const(self.expression_type(expr.value, state) or '') == 'Any':
                self.expression(ast.Member(expr.value, 'id'), state)
            return
        if isinstance(expr, ast.Index):
            if self.place(expr, state):
                if not address:
                    self.read(expr, state)
            else:
                self.expression(expr.base, state, address=address)
            self.expression(expr.index, state)
        elif isinstance(expr, (ast.Var, ast.Member)):
            if not address:
                self.read(expr, state)
            if isinstance(expr, ast.Member) and not self.place(expr, state):
                self.expression(expr.base, state)
        elif (isinstance(expr, ast.Cast)
              and raw_array(strip_const(self.expression_type(expr.operand, state) or ''))
              and (self.expression_type(expr, state) or '').endswith('*')):
            self.expression(expr.operand, state, address=True)
        elif isinstance(expr, ast.Move):
            self.expression(expr.operand, state)
            name = expr.operand.name
            state[name] = replace(state[name], moved=True)
        elif isinstance(expr, ast.UnaryOp) and expr.op == '&':
            self.expression(expr.operand, state, address=True)
        elif isinstance(expr, ast.Ternary):
            self.expression(expr.condition, state)
            left, right = dict(state), dict(state)
            self.expression(expr.then, left)
            self.expression(expr.orelse, right)
            state.update(merge_states([left, right]))
        elif isinstance(expr, ast.BinaryOp) and expr.op in ('and', 'or'):
            self.expression(expr.left, state)
            right = dict(state)
            self.expression(expr.right, right)
            state.update(merge_states([state, right]))
        elif isinstance(expr, ast.BlockExpr):
            self.block_expression(expr.body, state)
        elif isinstance(expr, ast.Try):
            self.expression(expr.result, state)
            if not expr.propagates:
                failed = dict(state)
                if expr.name:
                    typ = expr.try_plan.error_type
                    paths = self.required(typ)
                    failed[expr.name] = LocalState(typ, paths, paths)
                self.block_expression(expr.body or [], failed)
                merged = merge_states([state, failed])
                state.update({name: merged[name] for name in state})
        elif isinstance(expr, (ast.Call, ast.MethodCall)):
            plan = getattr(expr, 'call_plan', None)
            if plan and plan.replacement is not None:
                self.expression(plan.replacement, state)
                return
            receiver = (plan.receiver if plan and plan.passes_receiver else
                        expr.receiver if isinstance(expr, ast.MethodCall) else None)
            # Explicit initialization constructs into the receiver's storage.
            init = bool(plan and plan.kind != 'constructor' and plan.symbol
                        and '::init(' in plan.symbol)
            args = expr.args
            if init and receiver is None and args:
                receiver, args = args[0], args[1:]
            if receiver is not None:
                self.expression(receiver, state, address=init)
            if isinstance(expr, ast.Call) and expr.name in state:
                self.read(ast.Var(expr.name), state)
            for arg in args:
                self.expression(arg, state)
            if init:
                self.write(receiver, state)
        elif is_dataclass(expr):
            for field in fields(expr):
                child = getattr(expr, field.name)
                if isinstance(child, list):
                    for value in child:
                        if is_dataclass(value):
                            self.expression(value, state)
                elif is_dataclass(child):
                    self.expression(child, state)
        consumed = getattr(expr, 'consumed_local', None)
        if consumed in state:
            self.read(ast.Var(consumed), state)
            state[consumed] = replace(state[consumed], moved=True)

    def loop(self, stmt, state):
        """Solve loop back edges, including continue paths and for steps."""
        entry = dict(state)
        if isinstance(stmt, ast.For):
            entry = self.statement(stmt.init, entry)
        if isinstance(stmt, ast.Foreach):
            self.expression(stmt.foreach_plan.iterator_call or stmt.iterable, entry)
        if (not isinstance(stmt, ast.Foreach)
                and isinstance(stmt.condition, ast.BoolLiteral)
                and not stmt.condition.value):
            return {name: entry[name] for name in state}
        head = dict(entry)
        while True:
            body = dict(head)
            if not isinstance(stmt, ast.Foreach):
                self.expression(stmt.condition, body)
            else:
                typ = stmt.foreach_plan.element_reference_type
                paths = self.required(typ)
                body[stmt.name] = LocalState(typ, paths, paths,
                                            origin=frozenset({'local'}))
            breaks, continues = [], []
            self.loops.append((breaks, continues, len(self.defers)))
            try:
                end = self.block(stmt.body, body)
                backs = continues + ([end] if end is not None else [])
                if isinstance(stmt, ast.For):
                    backs = [self.statement(stmt.step, path) for path in backs]
            finally:
                self.loops.pop()
            next_head = merge_states([entry] + [p for p in backs if p is not None])
            next_head = {name: next_head[name] for name in entry}
            if next_head == head:
                # The condition may be false initially or after a back edge.
                exit_state = dict(head)
                if not isinstance(stmt, ast.Foreach):
                    self.expression(stmt.condition, exit_state)
                always = (not isinstance(stmt, ast.Foreach)
                          and isinstance(stmt.condition, ast.BoolLiteral)
                          and stmt.condition.value)
                result = merge_states(([] if always else [exit_state]) + breaks)
                return ({name: result[name] for name in state}
                        if result is not None else None)
            head = next_head

    def bind_pattern(self, pattern, types, state):
        """Initialize each local leaf of a checked tuple pattern."""
        for name, typ in zip(pattern, types):
            if isinstance(name, list):
                self.bind_pattern(name, typ, state)
            else:
                paths = self.required(typ)
                state[name] = LocalState(typ, paths, paths)

    def closure(self, expr, state):
        """Check a closure against the capture facts at its creation point."""
        fn = ast.Function(expr.name or '<closure>', expr.params,
                          expr.return_type, expr.body, line=expr.line,
                          file=expr.file)
        nested = LocalFlow(self.gen, fn, {})
        captures = {name: replace(value, storage=frozenset({'capture'}),
                                   origin=frozenset({'capture'}))
                    for name, value in state.items()}
        for index, param in enumerate(expr.params):
            paths = self.required(param.type)
            origin = frozenset({'first' if index == 0 else 'other'})
            captures[param.name] = LocalState(param.type, paths, paths,
                                               origin=origin, storage=origin)
            if param.pattern is not None:
                self.bind_pattern(param.pattern, param.pattern_types, captures)
        nested.block(expr.body, captures)

    def block_expression(self, body, state):
        """Merge the paths that emit a value from one block expression."""
        exits = []
        self.emits.append((exits, len(self.defers)))
        try:
            self.block(body, dict(state))
        finally:
            self.emits.pop()
        merged = merge_states(exits)
        if merged is not None:
            state.update({name: merged[name] for name in state})

    def flush_defers(self, state, depth):
        """Apply deferred accesses at their actual structured exit."""
        previous = self.in_defer
        self.in_defer = (len(self.loops), len(self.emits))
        try:
            for frame in reversed(self.defers[depth:]):
                for stmt, captured in reversed(frame):
                    view = dict(state)
                    for name, value in captured.items():
                        if name in view and view[name].binding != value.binding:
                            view[name] = value
                    self.statement(stmt, view)
                    for name in state:
                        if name in view and view[name].binding == state[name].binding:
                            state[name] = view[name]
        finally:
            self.in_defer = previous

    def block(self, statements, state):
        """Visit continuing statements and run this scope's deferred actions."""
        names = dict(state)
        shadowed = {}
        exit_marks = [(paths, len(paths))
                      for loop in self.loops for paths in loop[:2]]
        exit_marks += [(paths, len(paths)) for paths, _ in self.emits]
        self.defers.append([])
        try:
            for stmt in statements:
                if isinstance(stmt, ast.Let) and stmt.name in names:
                    shadowed[stmt.name] = (state[stmt.name], id(stmt))
                state = self.statement(stmt, state)
                if state is None:
                    return None
            self.flush_defers(state, len(self.defers) - 1)
            state.update({name: value for name, (value, _) in shadowed.items()})
            return {name: state[name] for name in names}
        finally:
            for paths, start in exit_marks:
                for path in paths[start:]:
                    for name, (value, binding) in shadowed.items():
                        if name in path and path[name].binding == binding:
                            path[name] = value
                    for name in list(path):
                        if name not in names:
                            del path[name]
            self.defers.pop()

    def statement(self, stmt, state):
        """Transfer local facts through one checked statement."""
        from siec.codegen.checking import assignment_target
        with source_location(line=getattr(stmt, 'line', 0), file=self.fn.file):
            if self.in_defer is not False:
                escapes = (isinstance(stmt, ast.Return)
                           or isinstance(stmt, ast.Emit) and len(self.emits) <= self.in_defer[1]
                           or isinstance(stmt, (ast.Break, ast.Continue))
                           and len(self.loops) <= self.in_defer[0])
                if escapes:
                    word = type(stmt).__name__.lower()
                    raise TypeError(f'a deferred statement cannot {word}')
            if isinstance(stmt, ast.Let):
                self.expression(stmt.value, state)
                sized = sized_array(stmt.type)
                typ = sized[0] if sized else stmt.type
                paths = (self.required(typ) if stmt.value is not None or sized
                         else self.defaults(typ))
                state[stmt.name] = LocalState(
                    typ, paths, paths,
                    origin=self.origins(stmt.value, state) if stmt.value else frozenset(),
                    binding=id(stmt))
            elif isinstance(stmt, (ast.Assign, ast.MemberAssign, ast.RefAssign, ast.IndexAssign)):
                target = (getattr(stmt, 'macro_target', None)
                          or getattr(stmt, 'checked_target', None)
                          or assignment_target(stmt))
                if isinstance(target, ast.Index):
                    if not self.place(target, state):
                        self.expression(target.base, state)
                    self.expression(target.index, state)
                elif not self.place(target, state):
                    self.expression(target, state, address=True)
                action = getattr(stmt, 'assignment_action', None)
                self.expression(action.call or action.value if action else stmt.value, state)
                self.write(target, state, self.origins(stmt.value, state))
            elif isinstance(stmt, ast.CompoundAssign):
                self.expression(stmt.target, state)
                self.expression(stmt.value, state)
            elif isinstance(stmt, ast.If):
                self.expression(stmt.condition, state)
                paths = [self.block(stmt.body, dict(state)),
                         self.block(stmt.orelse or [], dict(state))]
                return merge_states([path for path in paths if path is not None])
            elif isinstance(stmt, ast.Case):
                self.expression(stmt.subject, state)
                paths = [self.block(arm.body, dict(state)) for arm in stmt.arms]
                paths.append(self.block(stmt.orelse or [], dict(state)))
                return merge_states([path for path in paths if path is not None])
            elif isinstance(stmt, (ast.For, ast.While, ast.Foreach)):
                return self.loop(stmt, state)
            elif isinstance(stmt, ast.Block):
                return self.block(stmt.body, dict(state))
            elif isinstance(stmt, (ast.Return, ast.Emit)):
                if isinstance(stmt, ast.Return) and stmt.value is not None:
                    self.check_return(stmt.value, state)
                self.expression(stmt.value, state)
                if isinstance(stmt, ast.Emit) and self.emits:
                    exits, depth = self.emits[-1]
                    self.flush_defers(state, depth)
                    exits.append(dict(state))
                else:
                    self.flush_defers(state, 0)
                    if self.initializer:
                        self.read(ast.Var(self.fn.params[0].name), state)
                return None
            elif isinstance(stmt, (ast.Break, ast.Continue)):
                if self.loops:
                    self.flush_defers(state, self.loops[-1][2])
                    self.loops[-1][isinstance(stmt, ast.Continue)].append(dict(state))
                return None
            elif isinstance(stmt, ast.Drop):
                self.expression(stmt.target, state)
                place = self.place(stmt.target, state)
                if place and place[0] in state:
                    name, prefix = place
                    value = state[name]
                    dropped = {path for path in value.definite
                               if path[:len(prefix)] == prefix}
                    state[name] = replace(value, definite=value.definite - dropped,
                                          moved=not prefix or value.moved)
            elif isinstance(stmt, ast.ExprStmt):
                self.expression(stmt.expr, state)
                from siec.codegen.ownership import manually_destroyed_local
                name = manually_destroyed_local(stmt.expr)
                if name in state:
                    state[name] = replace(state[name], moved=True)
                plan = getattr(stmt.expr, 'call_plan', None)
                if plan and plan.symbol in self.gen.noreturns:
                    return None
            elif isinstance(stmt, ast.Defer):
                self.defers[-1].append((stmt.stmt, dict(state)))
            elif isinstance(stmt, ast.LetTuple):
                self.expression(stmt.value, state)
                self.bind_pattern(stmt.pattern, stmt.pattern_types, state)
            elif isinstance(stmt, ast.LocalFunction):
                self.closure(stmt.value, state)
                from siec.codegen.closures import closure_type

                typ = closure_type(stmt.value)
                paths = self.required(typ)
                state[stmt.name] = LocalState(typ, paths, paths)
        return state

    def check(self):
        """Start each parameter initialized and preserve its reference source."""
        state = {}
        first = self.fn.params[0].name if self.fn.params else None
        for name, variable in self.params.items():
            paths = self.required(variable.type)
            origin = frozenset({'first' if name == first else 'other'}) if is_reference(variable.type) else frozenset()
            if self.initializer and name == first:
                paths = self.defaults(variable.type)
            state[name] = LocalState(variable.type, paths, paths, origin=origin,
                                     storage=origin or frozenset({'local'}))
        for param in self.fn.params:
            if param.pattern is not None:
                self.bind_pattern(param.pattern, param.pattern_types, state)
        end = self.block(self.fn.body or [], state)
        if self.initializer and end is not None:
            self.read(ast.Var(first), end)


def check_local_flow(gen, fn, params):
    """Run local data-flow checks after expression and call resolution."""
    LocalFlow(gen, fn, params).check()
