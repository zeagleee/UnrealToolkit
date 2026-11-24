"""editor/scenario.py: rewriting cells with a top-level yield, waits, and stepping a Task (N6)."""
import ast
import time
import unittest
from unittest import mock

import support

scenario = support.load_editor_module("scenario")


def tree(code):
    return ast.parse(code)


def run_cell(code, namespace=None):
    """Compile a scenario cell the way the bridge does; returns (generator, namespace)."""
    namespace = {} if namespace is None else namespace
    exec(compile(scenario.wrap_scenario(tree(code)), "<cell>", "exec"), namespace)
    return namespace[scenario.SCENARIO_FN](), namespace


def run_task(task, max_steps=1000):
    for _ in range(max_steps):
        response = task.step()
        if response is not None:
            return response
    raise AssertionError("scenario did not finish")


class HasTopLevelYieldTest(unittest.TestCase):
    def test_top_level(self):
        self.assertTrue(scenario.has_top_level_yield(tree("x = 1\nyield 5\n")))
        self.assertTrue(scenario.has_top_level_yield(tree("value = yield ueh.until(f)\n")))
        self.assertTrue(scenario.has_top_level_yield(tree("yield from ueh.press('E')\n")))

    def test_inside_blocks_still_counts(self):
        self.assertTrue(scenario.has_top_level_yield(tree("for i in range(3):\n    yield 1\n")))
        self.assertTrue(scenario.has_top_level_yield(tree("if x:\n    yield\n")))
        self.assertTrue(scenario.has_top_level_yield(tree("try:\n    yield 1\nexcept Exception:\n    pass\n")))

    def test_nested_scopes_do_not_count(self):
        self.assertFalse(scenario.has_top_level_yield(tree("def gen():\n    yield 1\n")))
        self.assertFalse(scenario.has_top_level_yield(tree("async def gen():\n    yield 1\n")))
        self.assertFalse(scenario.has_top_level_yield(tree("f = lambda: (yield)\n")))
        self.assertFalse(scenario.has_top_level_yield(tree("class C:\n    def m(self):\n        yield 1\n")))

    def test_plain_cells(self):
        self.assertFalse(scenario.has_top_level_yield(tree("x = 1\nx + 1\n")))
        self.assertFalse(scenario.has_top_level_yield(tree("")))


class TopLevelBindingsTest(unittest.TestCase):
    def names(self, code):
        return scenario.top_level_bindings(tree(code).body)

    def test_assignments_and_definitions(self):
        self.assertEqual(self.names("a = 1\nb, c = 2, 3\nd += 1\nx.attr = 5\ny[0] = 1\n"), ["a", "b", "c", "d"])
        self.assertEqual(self.names("def f():\n    inner = 1\nclass K:\n    member = 2\n"), ["f", "K"])

    def test_imports_loops_and_handlers(self):
        code = ("import os.path\nimport json as j\nfrom math import pi, tau as t\nfor i in range(2):\n    pass\n"
                "with open(p) as fh:\n    pass\ntry:\n    pass\nexcept Exception as exc:\n    pass\n")
        self.assertEqual(self.names(code), ["os", "j", "pi", "t", "i", "fh", "exc"])

    def test_comprehension_and_lambda_scopes(self):
        self.assertEqual(self.names("total = sum(v for v in values)\nsq = [w * w for w in ws]\nf = lambda q: q\n"),
                         ["total", "sq", "f"])

    def test_global_statements(self):
        self.assertEqual(self.names("global shared\n"), ["shared"])

    def test_yield_assignments(self):
        self.assertEqual(self.names("box = yield ueh.until(find)\n"), ["box"])


class WrapScenarioTest(unittest.TestCase):
    def test_trailing_expression_is_the_result(self):
        generator, _ = run_cell("x = 2\nyield\nx * 21\n")
        next(generator)
        with self.assertRaises(StopIteration) as stop:
            next(generator)
        self.assertEqual(stop.exception.value, 42)

    def test_variables_land_in_the_namespace(self):
        namespace = {}
        generator, _ = run_cell("a = 1\nyield\nb = a + 1\n", namespace)
        for _ in generator:
            pass
        self.assertEqual((namespace["a"], namespace["b"]), (1, 2))

    def test_reads_existing_names(self):
        generator, _ = run_cell("yield\nseed * 2\n", {"seed": 21})
        next(generator)
        with self.assertRaises(StopIteration) as stop:
            next(generator)
        self.assertEqual(stop.exception.value, 42)

    def test_cell_with_its_own_global_line(self):
        namespace = {}
        generator, _ = run_cell("global g\ng = 3\nyield\n", namespace)
        for _ in generator:
            pass
        self.assertEqual(namespace["g"], 3)

    def test_sent_values(self):
        generator, _ = run_cell("got = yield 'need'\ngot + 1\n")
        self.assertEqual(next(generator), "need")
        with self.assertRaises(StopIteration) as stop:
            generator.send(41)
        self.assertEqual(stop.exception.value, 42)


class AsWaitTest(unittest.TestCase):
    def test_kinds(self):
        self.assertIsInstance(scenario.as_wait(None), scenario.Frames)
        self.assertIsInstance(scenario.as_wait(2), scenario.Seconds)
        self.assertIsInstance(scenario.as_wait(0.5), scenario.Seconds)
        frames = scenario.Frames(3)
        self.assertIs(scenario.as_wait(frames), frames)

    def test_refused(self):
        with self.assertRaisesRegex(TypeError, "bool"):
            scenario.as_wait(True)
        with self.assertRaisesRegex(TypeError, "can yield"):
            scenario.as_wait("five")


class WaitsTest(unittest.TestCase):
    def test_frames(self):
        wait = scenario.Frames(3)
        self.assertEqual([wait.poll()[0] for _ in range(3)], [False, False, True])
        self.assertTrue(scenario.Frames(0).poll()[0], "at least one frame, done after it")

    def test_seconds_on_a_fake_clock(self):
        clock = {"t": 10.0}
        wait = scenario.Seconds(2, clock=lambda: clock["t"])
        self.assertEqual(wait.poll(), (False, 0.0))
        clock["t"] = 11.5
        self.assertEqual(wait.poll(), (False, 1.5))
        clock["t"] = 0.25  # a new world: the clock starts over, and the wait doesn't stall
        self.assertFalse(wait.poll()[0])
        clock["t"] = 0.75
        done, elapsed = wait.poll()
        self.assertTrue(done)
        self.assertAlmostEqual(elapsed, 2.0)

    def test_seconds_fall_back_to_the_wall_clock(self):
        with mock.patch.object(scenario.time, "time", return_value=100.0):
            wait = scenario.Seconds(1, clock=lambda: None)
        with mock.patch.object(scenario.time, "time", return_value=100.5):
            self.assertFalse(wait.poll()[0])
        with mock.patch.object(scenario.time, "time", return_value=101.0):
            self.assertTrue(wait.poll()[0])

    def test_until_returns_the_value(self):
        values = iter([0, None, "found"])
        wait = scenario.Until(lambda: next(values), timeout=60)
        self.assertEqual(wait.poll(), (False, None))
        self.assertEqual(wait.poll(), (False, None))
        self.assertEqual(wait.poll(), (True, "found"))

    def test_until_times_out(self):
        with mock.patch.object(scenario.time, "time", return_value=0.0):
            wait = scenario.Until(lambda: 0, timeout=5, message="door open")
        with mock.patch.object(scenario.time, "time", return_value=6.0):
            with self.assertRaisesRegex(TimeoutError, r"until\(door open\).*5s.*last value 0"):
                wait.poll()

    def test_until_interval(self):
        calls = []
        with mock.patch.object(scenario.time, "time", return_value=0.0):
            wait = scenario.Until(lambda: calls.append(1), timeout=None, interval=1.0)
            wait.poll()
            wait.poll()
        self.assertEqual(len(calls), 1)
        with mock.patch.object(scenario.time, "time", return_value=1.0):
            wait.poll()
        self.assertEqual(len(calls), 2)


class TaskTest(unittest.TestCase):
    def test_steps_through_waits_to_the_result(self):
        clock = {"t": 0.0}
        generator, _ = run_cell("print('start')\nyield ueh_frames(2)\nyield 1.0\nv = yield ueh_until(lambda: ready)\n"
                                "print('got', v)\n{'v': v}\n",
                                {"ueh_frames": scenario.Frames, "ueh_until": scenario.Until, "ready": "yes"})
        task = scenario.Task(generator, request_id="r1", seconds_clock=lambda: clock["t"])
        self.assertIsNone(task.step())       # runs to the Frames(2)
        self.assertIsNone(task.step())       # frame 1 of 2
        self.assertIsNone(task.step())       # frame 2: runs on to the 1 s wait
        self.assertIsNone(task.step())       # 0 s of game time so far
        clock["t"] = 1.0
        self.assertIsNone(task.step())       # 1 s passed: runs on to until()
        response = task.step()               # the predicate is truthy at once
        self.assertIsNotNone(response)
        self.assertTrue(response["ok"], response["error"])
        self.assertEqual(response["result"], {"v": "yes"})
        self.assertEqual(response["result_repr"], "{'v': 'yes'}")
        self.assertEqual(response["stdout"], "start\ngot yes\n")
        self.assertEqual(response["scenario_steps"], 4)

    def test_exception_inside_reaches_the_caller(self):
        generator, _ = run_cell("yield\nraise ValueError('boom')\n")
        response = run_task(scenario.Task(generator))
        self.assertFalse(response["ok"])
        self.assertIn("ValueError: boom", response["error"])
        self.assertIsNone(response["result"])

    def test_timeout_is_raised_inside_and_can_be_caught(self):
        code = ("try:\n    yield wait_until(lambda: False, timeout=-1)\nexcept TimeoutError as exc:\n"
                "    caught = str(exc)\n'caught'\n")
        namespace = {"wait_until": scenario.Until}
        generator, _ = run_cell(code, namespace)
        response = run_task(scenario.Task(generator))
        self.assertTrue(response["ok"], response["error"])
        self.assertEqual(response["result"], "caught")
        self.assertIn("still false", namespace["caught"])

    def test_uncaught_timeout(self):
        generator, _ = run_cell("yield wait_until(lambda: False, timeout=-1)\n", {"wait_until": scenario.Until})
        response = run_task(scenario.Task(generator))
        self.assertFalse(response["ok"])
        self.assertIn("TimeoutError", response["error"])

    def test_bad_yield_is_raised_inside(self):
        generator, _ = run_cell("yield 'soon'\n")
        response = run_task(scenario.Task(generator))
        self.assertFalse(response["ok"])
        self.assertIn("TypeError", response["error"])

    def test_cancel(self):
        generator, _ = run_cell("yield 1000\n")
        task = scenario.Task(generator, seconds_clock=lambda: 0.0)
        self.assertIsNone(task.step())
        response = task.cancel()
        self.assertFalse(response["ok"])
        self.assertIn("cancelled by the client", response["error"])
        self.assertIn("[scenario cancelled by the client]", response["stdout"])

    def test_to_jsonable(self):
        generator, _ = run_cell("yield\n{1, 2}\n")
        response = run_task(scenario.Task(generator, to_jsonable=lambda v: sorted(v)))
        self.assertEqual(response["result"], [1, 2])

    def test_is_generator(self):
        generator, _ = run_cell("yield\n")
        self.assertTrue(scenario.is_generator(generator))
        self.assertFalse(scenario.is_generator([1]))


if __name__ == "__main__":
    unittest.main()
