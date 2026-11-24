"""Scenarios: Python cells that wait for the game between steps (the bridge runs them across editor ticks).

A cell becomes a scenario when it has a top-level `yield` (or its trailing expression is a generator):

    ueh.console('RMStartDay 1', pie=True)
    yield ueh.wait(5)                                   # 5 seconds of PIE game time
    box = yield ueh.until(lambda: find_box(), timeout=30)  # the truthy value comes back
    {'box': box.get_name()}                             # trailing expression = the result

The game keeps running between steps; a plain cell freezes it until the cell returns. What a scenario
can yield:
    None            -> resume next tick
    a number        -> that many seconds of game time (wall clock outside PIE), like ueh.wait(n)
    a wait object   -> anything with poll() -> (done, value); ueh.wait / until / frames / wait_real build them

No `unreal` import here, so the cell rewriting and task stepping can be tested outside the editor.
"""
import ast
import contextlib
import inspect
import io
import time
import traceback

SCENARIO_FN = "__uetk_scenario__"


# --------------------------------------------------------------------------- waits

class Wait(object):
    """Base for what a scenario yields. poll() is called every tick until it returns (True, value)."""

    def poll(self):
        return True, None

    def __repr__(self):
        return "<%s>" % type(self).__name__


class Frames(Wait):
    def __init__(self, count=1):
        self.left = max(1, int(count))

    def poll(self):
        self.left -= 1
        return self.left <= 0, None


class Seconds(Wait):
    """Seconds on `clock` (a function returning seconds or None); falls back to the wall clock when it returns None."""

    def __init__(self, seconds, clock=None):
        self.seconds = float(seconds)
        self.clock = clock
        self.real_start = time.time()
        self.clock_last = clock() if clock else None
        self.clock_elapsed = 0.0

    def poll(self):
        now = self.clock() if self.clock else None
        if now is None or self.clock_last is None:
            self.clock_last = now
            elapsed = time.time() - self.real_start
        else:
            # Accumulate deltas, so a new world (clock back to 0) doesn't stall the wait.
            if now >= self.clock_last:
                self.clock_elapsed += now - self.clock_last
            self.clock_last = now
            elapsed = self.clock_elapsed
        return elapsed >= self.seconds, elapsed

    def __repr__(self):
        return "<wait %gs>" % self.seconds


class Until(Wait):
    """Done when predicate() is truthy (its value is sent back); TimeoutError after `timeout` wall-clock seconds."""

    def __init__(self, predicate, timeout=60.0, message=None, interval=0.0):
        self.predicate = predicate
        self.timeout = timeout
        self.message = message
        self.interval = interval
        self.start = time.time()
        self.next_check = 0.0
        self.last_value = None

    def poll(self):
        now = time.time()
        if now >= self.next_check:
            self.next_check = now + self.interval
            value = self.predicate()
            self.last_value = value
            if value:
                return True, value
        if self.timeout is not None and now - self.start > self.timeout:
            what = self.message or getattr(self.predicate, "__name__", "condition")
            raise TimeoutError("until(%s): still false after %gs (last value %r)" % (what, self.timeout, self.last_value))
        return False, None

    def __repr__(self):
        return "<until %s>" % (self.message or getattr(self.predicate, "__name__", "condition"))


def as_wait(value, seconds_clock=None):
    """What a scenario yielded -> a Wait. Raises TypeError for anything else."""
    if value is None:
        return Frames(1)
    if isinstance(value, bool):
        raise TypeError("a scenario yielded a bool; yield ueh.until(lambda: ...) to wait for a condition")
    if isinstance(value, (int, float)):
        return Seconds(value, seconds_clock)
    if callable(getattr(value, "poll", None)):
        return value
    raise TypeError("a scenario can yield None, a number of seconds or a wait (ueh.wait/until/frames); got %r" % (value,))


# --------------------------------------------------------------------------- cell rewriting

class _YieldFinder(ast.NodeVisitor):
    def __init__(self):
        self.found = False

    def _skip(self, node):
        pass

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = visit_ClassDef = _skip

    def visit_Yield(self, node):
        self.found = True

    visit_YieldFrom = visit_Yield


def has_top_level_yield(tree):
    finder = _YieldFinder()
    for statement in tree.body:
        finder.visit(statement)
    return finder.found


class _Bindings(ast.NodeVisitor):
    """Names a cell binds at its top level (so a scenario's variables still land in the shared namespace)."""

    def __init__(self):
        self.names = []

    def _add(self, name):
        if name and name not in self.names:
            self.names.append(name)

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._add(node.id)

    def visit_FunctionDef(self, node):
        self._add(node.name)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
            self.visit(default)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._add(node.name)
        for item in node.bases + node.decorator_list + [k.value for k in node.keywords]:
            self.visit(item)

    def visit_Lambda(self, node):
        for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
            self.visit(default)

    def _comprehension(self, node):
        # Own scope: only the first iterable is evaluated outside it (the walrus is the rare exception, ignored).
        self.visit(node.generators[0].iter)

    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = _comprehension

    def visit_Import(self, node):
        for alias in node.names:
            self._add(alias.asname or alias.name.split(".")[0])

    visit_ImportFrom = visit_Import

    def visit_ExceptHandler(self, node):
        self._add(node.name)
        self.generic_visit(node)

    def visit_Global(self, node):
        for name in node.names:
            self._add(name)

    def visit_MatchAs(self, node):
        self._add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node):
        self._add(node.name)

    def visit_MatchMapping(self, node):
        self._add(getattr(node, "rest", None))
        self.generic_visit(node)


def top_level_bindings(statements):
    collector = _Bindings()
    for statement in statements:
        collector.visit(statement)
    return collector.names


def wrap_scenario(tree):
    """Module AST with a top-level yield -> module defining SCENARIO_FN(), a generator over the same statements.

    Names the cell binds are declared global, so they persist in the namespace like in a plain cell; a
    trailing expression becomes the return value (the scenario's result)."""
    names = top_level_bindings(tree.body)
    body = [s for s in tree.body if not isinstance(s, ast.Global)]  # the cell's own `global` lines would clash with ours
    if body and isinstance(body[-1], ast.Expr):
        last = body.pop()
        body.append(ast.copy_location(ast.Return(value=last.value), last))
    function = ast.parse("def %s():\n    pass\n" % SCENARIO_FN).body[0]
    function.body = ([ast.Global(names=names)] if names else []) + (body or [ast.Pass()])
    module = ast.Module(body=[function], type_ignores=[])
    return ast.fix_missing_locations(module)


# --------------------------------------------------------------------------- tasks

class Task(object):
    """A running scenario: step() is called every tick until it returns the finished response."""

    def __init__(self, generator, request_id=None, seconds_clock=None, to_jsonable=None):
        self.generator = generator
        self.id = request_id
        self.seconds_clock = seconds_clock
        self.to_jsonable = to_jsonable or (lambda value: value)
        self.out = io.StringIO()
        self.wait = None
        self.started_generator = False
        self.start = time.time()
        self.steps = 0

    @property
    def stdout(self):
        return self.out.getvalue()

    def _finish(self, result=None, error=None):
        with contextlib.suppress(Exception):
            self.generator.close()
        try:
            result_repr = None if result is None else repr(result)
        except Exception as exc:
            result_repr = "<repr failed: %s>" % exc
        return {
            "ok": error is None,
            "stdout": self.stdout,
            "result": self.to_jsonable(result),
            "result_repr": result_repr,
            "error": error,
            "seconds": round(time.time() - self.start, 3),
            "scenario_steps": self.steps,
        }

    def step(self):
        """Advance if the current wait is over. Returns the response dict when the scenario has ended, else None."""
        with contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.out):
            send, throw = None, None
            if self.wait is not None:
                try:
                    done, send = self.wait.poll()
                except BaseException as exc:  # a predicate failed or timed out: raise it inside the scenario
                    done, throw = True, exc
                if not done:
                    return None
            self.steps += 1
            try:
                if throw is not None:
                    yielded = self.generator.throw(throw)
                elif not self.started_generator:
                    self.started_generator = True
                    yielded = next(self.generator)
                else:
                    yielded = self.generator.send(send)
            except StopIteration as stop:
                return self._finish(result=stop.value)
            except BaseException:
                return self._finish(error=traceback.format_exc())
            try:
                self.wait = as_wait(yielded, self.seconds_clock)
            except TypeError as exc:
                self.wait = _Raise(exc)
            return None

    def cancel(self, reason="cancelled by the client"):
        self.out.write("\n[scenario %s]\n" % reason)
        return self._finish(error="Scenario %s after %.1fs (at %r)." % (reason, time.time() - self.start, self.wait))


class _Raise(Wait):
    def __init__(self, exc):
        self.exc = exc

    def poll(self):
        raise self.exc


def is_generator(value):
    return inspect.isgenerator(value)
