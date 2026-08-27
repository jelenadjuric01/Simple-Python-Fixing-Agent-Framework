"""Stage 2 — the finished project, and the guard that catches a model going in circles.

Everything here drives the REAL graph and the REAL tools in a real temp directory. Only the
model is scripted, so the whole file runs offline — no Ollama needed.

Four layers, in the order they depend on each other: the agent (graph, state, stop condition,
budget and guard, the wiring, and the oracle the whole design rests on), the model layer (the
scripted fake and the real client's configuration), the sandbox the tests execute in, and the
tools that are the agent's only way to touch the world. The CLI, the doctor and the eval harness
are not part of any of those, and are tested elsewhere.
"""

from __future__ import annotations

import inspect
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import ToolNode

from agentlang.config import DEFAULT_BASE_URL, DEFAULT_MODEL, LLMConfig
from agentlang.agent.graph import (
    MAX_GUARD_HITS,
    NUDGE,
    build_graph,
    call_signature,
    completion_tokens_of,
    run_agent,
    system_prompt,
)
from agentlang.agent.state import initial_state, keep_larger
from agentlang.agent.trace import Tracer, prompt_tokens_of
from agentlang.llm.client import make_chat_model
from agentlang.llm.fake import (
    FakeChatModel,
    assistant_text,
    assistant_tool_call,
    assistant_tool_calls,
)
from agentlang.runner import solve_task
from agentlang.sandbox.base import ExecResult, get_backend
from agentlang.sandbox.docker_backend import DEFAULT_IMAGE, DockerBackend
from agentlang.sandbox.subprocess_backend import SubprocessBackend
from agentlang.tasks.loader import Task
from agentlang.tools.base import TRUNCATION_MARKER, WorkspaceChanged
from agentlang.tools.fs import (
    ListFilesTool,
    PathEscapeError,
    ReadFileTool,
    WriteFileTool,
    is_test_path,
    relative_files,
    resolve_in_root,
)
from agentlang.tools.tests_tool import RunTestsTool

try:  # the prebuilt agent needs `langchain` itself, which is optional here
    from agentlang.agent.prebuilt import build_prebuilt_agent, prebuilt_solved

    PREBUILT_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on which packages are installed
    PREBUILT_AVAILABLE = False

UNITTEST_CMD = (sys.executable, "-m", "unittest", "discover", "-q")


class TempDirTestCase(unittest.TestCase):
    """A disposable directory in `self.tmp`, gone again however the test ends."""

    def setUp(self) -> None:
        # `.resolve()` is not decoration. On macOS mkdtemp hands back /var/folders/..., a symlink
        # to /private/var/folders/..., while every check in tools/fs.py compares against
        # `.resolve()` output. Without it those comparisons silently disagree.
        self.tmp = Path(tempfile.mkdtemp(prefix="agentlang_test_")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


def make_task(root: Path) -> Task:
    """A Task for an already-materialised project.

    `template_dir` points at `root` itself rather than a pristine `repo/` copy: the tools here
    are bound to the directory directly instead of going through `workspace()`, so there is
    nothing to copy from.
    """
    return Task(
        task_id=f"test_{root.name}",
        root=root,
        template_dir=root,
        test_command=UNITTEST_CMD,
        expected_failures=(),
        prompt="The test suite is failing. Find the bug and fix it.",
    )


# A literal "python" rather than sys.executable: this command is only ever handed to FakeBackend,
# which records it instead of running it, and one test asserts it arrives unchanged.
PYTHON_UNITTEST = ("python", "-m", "unittest", "discover", "-q")


class FakeBackend:
    """A backend that returns a canned result and records what it was asked to run.

    Satisfies the `ExecutionBackend` protocol structurally, without inheriting from it — which is
    the whole point of Protocol, and why `run_tests` can be tested with no subprocess at all.
    """

    def __init__(self, result: ExecResult | None = None) -> None:
        self.result = result or ExecResult(passed=False, output="1 failed", duration_s=0.01)
        self.calls: list[tuple[Path, tuple[str, ...], int]] = []

    def run(self, workspace: Path, command: tuple[str, ...], timeout_s: int = 10) -> ExecResult:
        self.calls.append((workspace, command, timeout_s))
        return self.result

# The one-file project every graph test starts from: red, and fixable by one line.
BUGGY = "def total(prices):\n    return sum(prices) - 1\n"
FIXED = "def total(prices):\n    return sum(prices)\n"
SUITE = (
    "import unittest\n\n"
    "from cart import total\n\n\n"
    "class TestCart(unittest.TestCase):\n"
    "    def test_total(self):\n"
    "        self.assertEqual(total([1, 2]), 3)\n"
)

# The real shipped fixture, for the tests that go through the full wiring rather than a
# hand-built temp project. Relative, because these run with the task directory as the cwd.
SHOPCART = Path("tasks/workshop/01-shopcart")
SHOPCART_FIXED = (
    "from shopcart.pricing import with_tax\n\n\n"
    "def subtotal(prices: list[float]) -> float:\n    return sum(prices)\n\n\n"
    "def total_with_tax(prices: list[float]) -> float:\n"
    "    return with_tax(subtotal(prices))\n"
)
PASSING_SUITE = (
    "import unittest\n\n\nclass TestCart(unittest.TestCase):\n"
    "    def test_ok(self):\n        self.assertTrue(True)\n"
)


class TestCallSignature(unittest.TestCase):
    """The identity function. One line, and one trap."""

    def test_the_same_call_twice_has_the_same_signature(self):
        call = {"name": "read_file", "args": {"path": "cart.py"}, "id": "c1"}
        other = {"name": "read_file", "args": {"path": "cart.py"}, "id": "c2"}
        self.assertEqual(call_signature(call), call_signature(other))
        self.assertNotIn("c1", call_signature(call), "the id must not be part of the identity")

    def test_different_arguments_are_a_different_call(self):
        a = {"name": "read_file", "args": {"path": "cart.py"}, "id": "c1"}
        b = {"name": "read_file", "args": {"path": "other.py"}, "id": "c2"}
        self.assertNotEqual(call_signature(a), call_signature(b))

    def test_a_different_tool_is_a_different_call(self):
        a = {"name": "read_file", "args": {}, "id": "c1"}
        b = {"name": "list_files", "args": {}, "id": "c2"}
        self.assertNotEqual(call_signature(a), call_signature(b))

    def test_key_order_does_not_change_the_signature(self):
        """The trap. JSON object key order is not meaningful, and a model will reorder keys.

        A signature built by stringifying the dict as-is lets a stuck model walk straight past
        the guard by shuffling its arguments.
        """
        a = {"name": "write_file", "args": {"path": "a.py", "content": "x = 1\n"}, "id": "c1"}
        b = {"name": "write_file", "args": {"content": "x = 1\n", "path": "a.py"}, "id": "c2"}
        self.assertEqual(call_signature(a), call_signature(b))

    def test_a_call_with_no_arguments_works(self):
        """run_tests and list_files take none, so this must not raise."""
        self.assertTrue(call_signature({"name": "run_tests", "args": {}, "id": "c1"}))


class Stage2TestCase(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        (self.tmp / "cart.py").write_text(BUGGY, encoding="utf-8")
        (self.tmp / "test_cart.py").write_text(SUITE, encoding="utf-8")
        self.task = make_task(self.tmp)
        # Kept as an attribute, not just a list entry: one test replaces its `_run` to observe
        # whether a test run can overlap a write in the same turn.
        self.run_tests = RunTestsTool(
            root=self.tmp,
            command=UNITTEST_CMD,
            backend=SubprocessBackend(),
            timeout_s=30,
        )
        self.tools = [
            ListFilesTool(root=self.tmp),
            ReadFileTool(root=self.tmp),
            WriteFileTool(root=self.tmp),
            self.run_tests,
        ]

    def run_with(self, replies, max_steps=None, tracer=None):
        replies = list(replies)
        llm = FakeChatModel(replies=replies)
        result = run_agent(
            self.task,
            llm,
            self.tools,
            max_steps=len(replies) if max_steps is None else max_steps,
            tracer=tracer,
        )
        return result, llm


class TestTheGuard(Stage2TestCase):
    def test_an_identical_repeated_call_is_not_executed_again(self):
        """Re-running it would spend a step to learn nothing. Say so instead."""
        tracer = Tracer()
        self.run_with(
            [
                assistant_tool_call("list_files", {}, call_id="c1"),
                assistant_tool_call("list_files", {}, call_id="c2"),
                assistant_text("done"),
            ],
            max_steps=3,
            tracer=tracer,
        )
        guarded = [e for e in tracer.events if "guarded" in e.detail]
        self.assertEqual(len(guarded), 1, "the second identical call must be refused, not run")

    def test_the_refused_call_still_gets_an_answer(self):
        """The rule you must not break: one reply per call, including the refusals.

        Skip the `tool_call_id` and the model's next request is rejected by the API — a failure
        that surfaces one turn away from the code that caused it.
        """
        _, llm = self.run_with(
            [
                assistant_tool_call("list_files", {}, call_id="first"),
                assistant_tool_call("list_files", {}, call_id="second"),
                assistant_text("done"),
            ],
            max_steps=3,
        )
        answered = [m.tool_call_id for m in llm.calls[-1] if getattr(m, "tool_call_id", None)]
        self.assertEqual(answered, ["first", "second"])

    def test_the_model_is_told_why_nothing_happened(self):
        _, llm = self.run_with(
            [
                assistant_tool_call("list_files", {}, call_id="c1"),
                assistant_tool_call("list_files", {}, call_id="c2"),
                assistant_text("done"),
            ],
            max_steps=3,
        )
        answers = [str(m.content) for m in llm.calls[-1] if getattr(m, "tool_call_id", None)]
        self.assertTrue(
            any("already called" in a for a in answers),
            "an unexplained refusal teaches the model nothing",
        )

    def test_key_order_does_not_defeat_the_guard_end_to_end(self):
        tracer = Tracer()
        self.run_with(
            [
                assistant_tool_call(
                    "write_file", {"path": "a.py", "content": "x = 1\n"}, call_id="c1"
                ),
                assistant_tool_call(
                    "write_file", {"content": "x = 1\n", "path": "a.py"}, call_id="c2"
                ),
                assistant_text("done"),
            ],
            max_steps=3,
            tracer=tracer,
        )
        self.assertTrue(any("guarded" in e.detail for e in tracer.events))

    def test_making_progress_resets_the_counter(self):
        """A → B → A is not a stuck model. Only consecutive repeats are."""
        tracer = Tracer()
        self.run_with(
            [
                assistant_tool_call("list_files", {}, call_id="c1"),
                assistant_tool_call("run_tests", {}, call_id="c2"),
                assistant_tool_call("list_files", {}, call_id="c3"),
                assistant_text("done"),
            ],
            max_steps=4,
            tracer=tracer,
        )
        self.assertFalse(
            any("guarded" in e.detail for e in tracer.events),
            "nothing here repeats consecutively",
        )

    def test_a_stuck_model_is_abandoned_rather_than_burning_the_budget(self):
        """The payoff. Ten identical calls must not cost ten model turns."""
        result, llm = self.run_with(
            [assistant_tool_call("list_files", {}, call_id=f"c{i}") for i in range(10)],
            max_steps=10,
        )
        self.assertFalse(result.solved)
        self.assertLessEqual(
            llm.index, MAX_GUARD_HITS + 1, "the run should have been given up on, not run out"
        )


class TestTheAgentStillWorks(Stage2TestCase):
    def test_a_normal_run_is_untouched_by_the_guard(self):
        """The guard must be invisible to an agent that is making progress."""
        fixed = "def total(prices):\n    return sum(prices)\n"
        result, _ = self.run_with(
            [
                assistant_tool_call("run_tests", {}),
                assistant_tool_call("read_file", {"path": "cart.py"}),
                assistant_tool_call("write_file", {"path": "cart.py", "content": fixed}),
                assistant_tool_call("run_tests", {}),
                assistant_text("Fixed it."),
            ]
        )
        self.assertTrue(result.solved)


class TestKeepLarger(unittest.TestCase):
    def test_the_larger_value_wins_in_both_directions(self):
        self.assertEqual(keep_larger(5, 3), 5)
        self.assertEqual(keep_larger(3, 5), 5)
        self.assertEqual(keep_larger(4, 4), 4)

    def test_a_high_water_mark_starts_correctly_from_zero(self):
        self.assertEqual(keep_larger(0, 120), 120)
        self.assertEqual(initial_state("s", "t")["peak_prompt_tokens"], 0)

    def test_the_builtin_max_cannot_be_used_as_a_reducer(self):
        """The reason this function exists, asserted so nobody "simplifies" it back.

        LangGraph inspects a reducer's signature to recognise it as a two-argument combiner, and
        `inspect.signature` raises on a C builtin — so `Annotated[int, max]` fails at StateGraph
        construction with "no signature found for builtin max".
        """
        with self.assertRaises(ValueError):
            inspect.signature(max)
        self.assertIsNotNone(inspect.signature(keep_larger))


class TestUsageAccessors(unittest.TestCase):
    def test_a_reply_with_no_usage_metadata_costs_zero_rather_than_raising(self):
        """Not every server reports usage; the accounting must degrade, not crash."""
        bare = AIMessage(content="hello")
        self.assertEqual(prompt_tokens_of(bare), 0)
        self.assertEqual(completion_tokens_of(bare), 0)

    def test_usage_is_read_from_the_metadata_when_present(self):
        message = AIMessage(
            content="hi",
            usage_metadata={"input_tokens": 120, "output_tokens": 7, "total_tokens": 127},
        )
        self.assertEqual(prompt_tokens_of(message), 120)
        self.assertEqual(completion_tokens_of(message), 7)


class TestSystemPrompt(Stage2TestCase):
    def test_the_tool_names_come_from_the_registered_tools(self):
        """Derived, not hardcoded, so the prompt and the schemas cannot drift apart."""
        prompt = system_prompt(self.tools)
        for name in ("list_files", "read_file", "write_file", "run_tests"):
            self.assertIn(name, prompt)


class TestHappyPath(Stage2TestCase):
    def test_the_agent_solves_the_task_and_reports_what_it_cost(self):
        result, llm = self.run_with(
            [
                assistant_tool_call("run_tests", {}),
                assistant_tool_call("read_file", {"path": "cart.py"}),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("Fixed the off-by-one."),
            ]
        )
        self.assertTrue(result.solved)
        self.assertEqual(result.steps_used, 5)
        self.assertEqual(llm.index, 5, "the graph took exactly the scripted turns")
        self.assertEqual((self.tmp / "cart.py").read_text(), FIXED)
        self.assertGreater(result.prompt_tokens, 0)
        self.assertGreater(result.peak_prompt_tokens, 0)

    def test_the_token_accounting_sums_and_the_peak_is_a_high_water_mark(self):
        """Pins all three numeric reducers at once.

        Without this, dropping `operator.add` from the token counters or `keep_larger` from the
        peak changed nothing that any test noticed — the only assertions were `> 0`.
        """
        result, _ = self.run_with(
            [
                assistant_tool_call("run_tests", {}, prompt_tokens=100),
                assistant_tool_call("list_files", {}, prompt_tokens=300),
                assistant_text("done", prompt_tokens=200),
            ]
        )
        self.assertEqual(result.prompt_tokens, 600, "the bill is the sum of every turn")
        self.assertEqual(result.peak_prompt_tokens, 300, "the peak is the largest single prompt")
        self.assertGreater(result.completion_tokens, 0)

    def test_the_history_is_append_only(self):
        """Byte-stable prefix -> the server's KV cache stays valid across turns."""
        _, llm = self.run_with(
            [
                assistant_tool_call("run_tests", {}),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("done"),
            ]
        )
        for earlier, later in zip(llm.calls, llm.calls[1:]):
            self.assertEqual(later[: len(earlier)], earlier)

    def test_the_tool_call_id_is_carried_back(self):
        """The API pairs each answer to its question by id; skip one and the next call fails."""
        _, llm = self.run_with(
            [
                assistant_tool_call("run_tests", {}, call_id="abc123"),
                assistant_text("done"),
            ]
        )
        answers = [m for m in llm.calls[-1] if getattr(m, "tool_call_id", None)]
        self.assertEqual([m.tool_call_id for m in answers], ["abc123"])

    def test_several_calls_in_one_turn_are_all_answered(self):
        """A turn answering only the first call would otherwise pass every test."""
        _, llm = self.run_with(
            [
                assistant_tool_calls(
                    [("run_tests", {}), ("list_files", {})], call_ids=("c1", "c2")
                ),
                assistant_text("done"),
            ]
        )
        ids = [m.tool_call_id for m in llm.calls[-1] if getattr(m, "tool_call_id", None)]
        self.assertEqual(ids, ["c1", "c2"])


class TestFailuresBecomeObservations(Stage2TestCase):
    """Every way a model can get a tool call wrong must be recoverable, not fatal."""

    def _last_tool_content(self, llm) -> str:
        tools = [m for m in llm.calls[-1] if getattr(m, "tool_call_id", None)]
        return str(tools[-1].content)

    def test_a_hallucinated_tool_name_does_not_end_the_run(self):
        result, llm = self.run_with(
            [
                assistant_tool_call("frobnicate", {"x": 1}),
                assistant_text("giving up"),
            ]
        )
        self.assertFalse(result.solved)
        self.assertIn("frobnicate", self._last_tool_content(llm))

    def test_a_missing_required_argument_does_not_end_the_run(self):
        _, llm = self.run_with(
            [
                assistant_tool_call("read_file", {}),
                assistant_text("giving up"),
            ]
        )
        content = self._last_tool_content(llm).lower()
        self.assertTrue("path" in content or "required" in content)

    def test_a_tool_that_raises_does_not_end_the_run(self):
        """ToolNode's default lets the exception through; the graph opts back in."""
        broken = ReadFileTool(root=self.tmp)

        def explode(**_kwargs):
            raise RuntimeError("disk on fire")

        object.__setattr__(broken, "_run", explode)
        self.tools[1] = broken
        result, llm = self.run_with(
            [
                assistant_tool_call("read_file", {"path": "cart.py"}),
                assistant_text("giving up"),
            ]
        )
        self.assertFalse(result.solved)
        self.assertTrue(self._last_tool_content(llm))


class TestStopCondition(Stage2TestCase):
    def test_not_done_before_the_tests_have_ever_run(self):
        """The verdict starts False, so an agent that never runs the tests never solves."""
        result, _ = self.run_with([assistant_text("looks fine to me")], max_steps=1)
        self.assertFalse(result.solved)

    def test_not_done_when_the_model_only_claims_success(self):
        """The whole point: a model announcing victory is not evidence."""
        result, _ = self.run_with(
            [
                assistant_text("I have fixed the bug."),
                assistant_text("Really, it is fixed."),
            ],
            max_steps=2,
        )
        self.assertFalse(result.solved)

    def test_done_only_once_the_tests_actually_pass(self):
        result, _ = self.run_with(
            [
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("done"),
            ]
        )
        self.assertTrue(result.solved)

    def test_a_prose_reply_while_red_is_nudged_rather_than_accepted(self):
        _, llm = self.run_with(
            [
                assistant_tool_call("run_tests", {}),
                assistant_text("I think it is fine now."),
                assistant_text("still fine"),
            ],
            max_steps=3,
        )
        self.assertTrue(any(getattr(m, "content", None) == NUDGE for m in llm.calls[-1]))

    def test_a_write_invalidates_a_previously_green_result(self):
        """Otherwise: run tests, pass, then break them, and the verdict still says SOLVED."""
        result, _ = self.run_with(
            [
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_tool_call("write_file", {"path": "cart.py", "content": BUGGY}),
                assistant_text("done"),
            ]
        )
        self.assertFalse(result.solved, "a stale green result must not survive a write")

    def test_a_write_after_a_green_run_in_the_same_turn_ends_red(self):
        """One message, two calls, and the ORDER of the fold decides the verdict.

        An order-insensitive `tests_passed_after` passes every other test in this suite, so
        without this the fold could be "simplified" into reporting SOLVED for code that was
        never measured.
        """
        result, _ = self.run_with(
            [
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                # A different call in between, or the loop guard refuses the run_tests below as
                # a repeat and it never executes — which is what defeated the first version of
                # this test: no ExecResult was produced, so the fold order never mattered.
                assistant_tool_call("list_files", {}),
                # Green, then broken again, inside one turn.
                assistant_tool_calls(
                    [("run_tests", {}), ("write_file", {"path": "cart.py", "content": BUGGY})],
                    call_ids=("c1", "c2"),
                ),
                assistant_text("done"),
            ]
        )
        self.assertFalse(result.solved, "the write came last, so nothing has measured this code")
        self.assertEqual((self.tmp / "cart.py").read_text(), BUGGY, "the write did happen")

    def test_a_run_after_a_write_in_the_same_turn_ends_green(self):
        """The mirror image: measured last, so the measurement stands."""
        result, _ = self.run_with(
            [
                assistant_tool_calls(
                    [("write_file", {"path": "cart.py", "content": FIXED}), ("run_tests", {})],
                    call_ids=("c1", "c2"),
                ),
                assistant_text("done"),
            ]
        )
        self.assertTrue(result.solved)

    def test_a_turn_that_changes_nothing_leaves_a_green_verdict_alone(self):
        """`tests_passed_after` is seeded with the verdict so far, not with False.

        list_files neither measures nor modifies anything, so a green result stays green across
        it. Reseeding from False each turn would nudge an agent that had already succeeded.
        """
        result, _ = self.run_with(
            [
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_tool_call("list_files", {}),
                assistant_text("done"),
            ]
        )
        self.assertTrue(result.solved)

    def test_the_agent_cannot_fake_success_by_rewriting_the_tests(self):
        result, _ = self.run_with(
            [
                assistant_tool_call(
                    "write_file",
                    {"path": "test_cart.py", "content": "import unittest\n"},
                ),
                assistant_tool_call("run_tests", {}),
                assistant_text("done"),
            ]
        )
        self.assertFalse(result.solved)
        self.assertIn("assertEqual", (self.tmp / "test_cart.py").read_text())


class TestBudgetAndGuard(Stage2TestCase):
    def test_the_step_budget_is_respected(self):
        result, llm = self.run_with(
            [assistant_tool_call("list_files", {}) for _ in range(10)], max_steps=3
        )
        self.assertEqual(result.steps_used, 3)
        self.assertEqual(llm.index, 3, "the graph must not exceed its budget")

    def test_an_identical_repeated_call_is_not_executed_again(self):
        tracer = Tracer()
        self.run_with(
            [
                assistant_tool_call("list_files", {}, call_id="c1"),
                assistant_tool_call("list_files", {}, call_id="c2"),
                assistant_text("done"),
            ],
            max_steps=3,
            tracer=tracer,
        )
        guarded = [e for e in tracer.events if "guarded" in e.detail]
        self.assertEqual(len(guarded), 1)

    def test_key_order_does_not_defeat_the_guard(self):
        """{"a":1,"b":2} and {"b":2,"a":1} are the same call."""
        tracer = Tracer()
        self.run_with(
            [
                assistant_tool_call(
                    "write_file", {"path": "a.py", "content": "x = 1\n"}, call_id="c1"
                ),
                assistant_tool_call(
                    "write_file", {"content": "x = 1\n", "path": "a.py"}, call_id="c2"
                ),
                assistant_text("done"),
            ],
            max_steps=3,
            tracer=tracer,
        )
        self.assertTrue(any("guarded" in e.detail for e in tracer.events))

    def test_a_stuck_model_is_abandoned_rather_than_looping_to_the_budget(self):
        result, llm = self.run_with(
            [assistant_tool_call("list_files", {}, call_id=f"c{i}") for i in range(10)],
            max_steps=10,
        )
        self.assertFalse(result.solved)
        self.assertLessEqual(llm.index, MAX_GUARD_HITS + 1)

    def test_the_second_repeat_warns_that_the_run_will_be_abandoned(self):
        """Only the first wording was pinned; the escalation could be deleted unnoticed."""
        tracer = Tracer()
        _, llm = self.run_with(
            [assistant_tool_call("list_files", {}, call_id=f"c{i}") for i in range(4)],
            max_steps=4,
            tracer=tracer,
        )
        answers = [str(m.content) for m in llm.calls[-1] if getattr(m, "tool_call_id", None)]
        self.assertTrue(
            any("abandoned" in a and str(MAX_GUARD_HITS) in a for a in answers),
            "the escalated observation names the consequence",
        )

    def test_making_progress_resets_the_guard(self):
        tracer = Tracer()
        self.run_with(
            [
                assistant_tool_call("list_files", {}, call_id="c1"),
                assistant_tool_call("run_tests", {}, call_id="c2"),
                assistant_tool_call("list_files", {}, call_id="c3"),
                assistant_text("done"),
            ],
            max_steps=4,
            tracer=tracer,
        )
        self.assertFalse(any("guarded" in e.detail for e in tracer.events))


class TestToolNodeContract(Stage2TestCase):
    def test_the_calls_in_one_turn_execute_one_at_a_time(self):
        """The oracle guarantee: a test run must never race a write in the same turn.

        ToolNode batches through a real ThreadPoolExecutor, so without `max_concurrency=1` the
        calls in one message run concurrently and `run_tests` can measure the file as it was
        BEFORE a write in the same turn — a green verdict for code that no longer exists.
        Message order is preserved either way, so nothing in the trace would look wrong.
        """
        timeline: list[str] = []
        slow_write = WriteFileTool(root=self.tmp)
        original = slow_write._run

        def traced(path: str, content: str):
            timeline.append("write-start")
            time.sleep(0.3)
            result = original(path=path, content=content)
            timeline.append("write-end")
            return result

        object.__setattr__(slow_write, "_run", traced)
        self.tools[2] = slow_write

        real_run = self.run_tests._run

        def traced_run():
            timeline.append("run-start")
            out = real_run()
            timeline.append("run-end")
            return out

        object.__setattr__(self.run_tests, "_run", traced_run)

        self.run_with(
            [
                assistant_tool_calls(
                    [("write_file", {"path": "cart.py", "content": FIXED}), ("run_tests", {})],
                    call_ids=("c1", "c2"),
                ),
                assistant_text("done"),
            ],
            max_steps=2,
        )
        self.assertEqual(
            timeline,
            ["write-start", "write-end", "run-start", "run-end"],
            "the tests must not begin until the write has finished",
        )

    def test_a_dropped_tool_answer_raises_rather_than_corrupting_the_next_request(self):
        """This invariant used to be an `assert`, which `python -O` removes.

        If ToolNode ever answers fewer calls than it was given, the API rejects the NEXT
        request — a turn away from the cause. Fail here instead, with the count.
        """
        with mock.patch.object(ToolNode, "invoke", return_value={"messages": []}):
            with self.assertRaises(RuntimeError) as ctx:
                self.run_with(
                    [assistant_tool_call("run_tests", {}), assistant_text("done")], max_steps=2
                )
        self.assertIn("must get exactly one reply", str(ctx.exception))
        self.assertIn("0 of 1", str(ctx.exception))


class TestCheckpointing(Stage2TestCase):
    """State the framework can snapshot, which only works if the state holds everything."""

    def _run(self, app, llm, state=None):
        """Invoke `app` on thread "t". `state=None` starts a run; a partial dict resumes one."""
        return app.invoke(
            initial_state(system_prompt(self.tools), "Fix it.") if state is None else state,
            config={"configurable": {"thread_id": "t"}, "callbacks": [Tracer()]},
        )

    def test_a_run_can_be_resumed_from_its_checkpoint(self):
        """The verdict has to live in the state, or a resumed solved task comes back unsolved.

        Two invocations against one saver: the fix is written in the first, the verifying test
        run happens in the second, and the verdict crosses the gap because it is checkpointed
        rather than held on a tool object that the second graph rebuilds empty.
        """
        saver = InMemorySaver()
        first = FakeChatModel(
            replies=[assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED})]
        )
        app = build_graph(first, self.tools, Tracer(), max_steps=1, checkpointer=saver)
        interrupted = self._run(app, first)
        self.assertFalse(interrupted["tests_passed"], "written but not yet verified")

        # A fresh model, a fresh graph, fresh tools — and `{"messages": []}` as the input, which
        # adds nothing and so leaves every checkpointed value in place. This is a resume, not a
        # second run: the history the model receives is the one from above.
        second = FakeChatModel(
            replies=[assistant_tool_call("run_tests", {}), assistant_text("done")]
        )
        resumed = build_graph(second, self.tools, Tracer(), max_steps=3, checkpointer=saver)
        final = self._run(resumed, second, state={"messages": []})

        self.assertTrue(final["tests_passed"], "the resumed run must see its own green suite")
        self.assertGreaterEqual(
            len(second.calls[0]),
            len(interrupted["messages"]),
            "the resumed model was sent the checkpointed history, not a bare prompt",
        )

        # Compared by content rather than by message equality: the checkpointer's serialiser
        # round-trips a frozen dataclass artifact back as a plain dict, so a replayed
        # ToolMessage is identical in everything the model sees but not `==` to the original.
        self.assertEqual(
            [m.content for m in final["messages"][: len(interrupted["messages"])]],
            [m.content for m in interrupted["messages"]],
        )
        # The step budget is per-invocation, so the resumed run counted its own turns on top.
        self.assertGreater(final["step"], interrupted["step"])

    def test_the_verdict_itself_survives_a_resume(self):
        """The test that would have caught the original bug, and the one I first got wrong.

        `test_a_run_can_be_resumed_from_its_checkpoint` re-runs the suite after resuming, so its
        green verdict is re-derived rather than restored — it would pass even if the verdict were
        not checkpointed at all. Here run 2 never calls a tool, so the ONLY possible source of
        `tests_passed` is the checkpoint.
        """
        saver = InMemorySaver()
        first = FakeChatModel(
            replies=[
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
            ]
        )
        app = build_graph(first, self.tools, Tracer(), max_steps=2, checkpointer=saver)
        self.assertTrue(self._run(app, first)["tests_passed"])

        second = FakeChatModel(replies=[assistant_text("already fixed")])
        resumed = build_graph(second, self.tools, Tracer(), max_steps=1, checkpointer=saver)
        final = self._run(resumed, second, state={"messages": []})

        self.assertTrue(final["tests_passed"], "the verdict was not carried across the resume")
        self.assertEqual(second.index, 1, "run 2 took exactly one turn")
        self.assertFalse(
            [m for m in second.calls[0] if getattr(m, "name", None) == "run_tests"][1:],
            "run 2 must not have re-measured anything of its own",
        )

    def test_every_step_is_recoverable_from_the_history(self):
        """`get_state_history` is what makes a run inspectable after the fact."""
        saver = InMemorySaver()
        llm = FakeChatModel(
            replies=[
                assistant_tool_call("run_tests", {}),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
            ]
        )
        app = build_graph(llm, self.tools, Tracer(), max_steps=3, checkpointer=saver)
        self._run(app, llm)
        config = {"configurable": {"thread_id": "t"}}
        # `.get`, not `[...]`: the history includes a snapshot taken before any node ran, whose
        # values are just the input.
        verdicts = [
            snapshot.values.get("tests_passed") for snapshot in app.get_state_history(config)
        ]
        self.assertIn(True, verdicts, "the green run is in the history")
        self.assertIn(False, verdicts, "so is the red one it started from")


class TestTracing(Stage2TestCase):
    def test_every_model_turn_and_tool_call_is_recorded(self):
        tracer = Tracer()
        result, _ = self.run_with(
            [
                assistant_tool_call("run_tests", {}),
                assistant_text("done"),
            ],
            max_steps=2,
            tracer=tracer,
        )
        self.assertEqual([e.kind for e in tracer.events], ["llm", "tool", "llm"])
        self.assertEqual(result.trace, tuple(tracer.events))

    def test_a_tool_line_reports_the_context_size_of_the_turn_that_asked_for_it(self):
        tracer = Tracer()
        self.run_with(
            [
                assistant_tool_call("run_tests", {}, prompt_tokens=1234),
                assistant_text("done"),
            ],
            max_steps=2,
            tracer=tracer,
        )
        tool_event = next(e for e in tracer.events if e.kind == "tool")
        self.assertEqual(tool_event.prompt_tokens, 1234)

    def test_the_absence_of_reasoning_is_visible(self):
        """Measured on Mellum2: a tool-calling turn carries no reasoning text at all."""
        tracer = Tracer()
        self.run_with(
            [
                assistant_tool_call("run_tests", {}),
                assistant_text("done"),
            ],
            max_steps=2,
            tracer=tracer,
        )
        self.assertIn("NO REASONING", tracer.events[0].detail)

    def test_reasoning_is_shown_when_the_model_does_emit_it(self):
        tracer = Tracer()
        self.run_with(
            [
                assistant_tool_calls([("run_tests", {})], text="Let me see what fails first."),
                assistant_text("done"),
            ],
            max_steps=2,
            tracer=tracer,
        )
        self.assertIn("Let me see what fails first.", tracer.events[0].detail)


class TestSolveTask(TempDirTestCase):
    def test_the_real_wiring_solves_a_real_fixture_with_a_scripted_model(self):
        llm = FakeChatModel(
            replies=[
                assistant_tool_call("run_tests", {}),
                assistant_tool_call("read_file", {"path": "shopcart/cart.py"}),
                assistant_tool_call("write_file", {"path": "shopcart/cart.py", "content": SHOPCART_FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("Fixed the tax rounding."),
            ]
        )
        result = solve_task(SHOPCART, llm=llm, max_steps=5)
        self.assertTrue(result.solved)
        self.assertEqual(result.task_id, "01-shopcart")

    def test_the_pristine_fixture_is_byte_identical_afterwards(self):
        """The agent rewrites whole files; the next run must start from the same bug."""
        source = SHOPCART / "repo" / "shopcart" / "cart.py"
        before = source.read_bytes()
        llm = FakeChatModel(
            replies=[
                assistant_tool_call("write_file", {"path": "shopcart/cart.py", "content": SHOPCART_FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("done"),
            ]
        )
        solve_task(SHOPCART, llm=llm, max_steps=3)
        self.assertEqual(source.read_bytes(), before)

    def test_the_tools_are_bound_to_the_workspace_not_the_repo(self):
        """A read of a repo-relative path must resolve inside the disposable copy."""
        llm = FakeChatModel(
            replies=[
                assistant_tool_call("list_files", {}),
                assistant_text("done"),
            ]
        )
        result = solve_task(SHOPCART, llm=llm, max_steps=2)
        listing = next(e.detail for e in result.trace if e.name == "list_files")
        self.assertIn("shopcart/cart.py", listing)
        self.assertNotIn(str(SHOPCART.resolve()), listing)

    def test_a_write_invalidates_the_last_test_result(self):
        """write_file reports WorkspaceChanged; without it a stale green result survives."""
        llm = FakeChatModel(
            replies=[
                assistant_tool_call("write_file", {"path": "shopcart/cart.py", "content": SHOPCART_FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_tool_call(
                    "write_file",
                    {
                        "path": "shopcart/cart.py",
                        "content": "def total_with_tax(p):\n    return 0\n",
                    },
                ),
                assistant_text("done"),
            ]
        )
        self.assertFalse(solve_task(SHOPCART, llm=llm, max_steps=4).solved)


class TestCaseInsensitiveBypass(TempDirTestCase):
    """macOS is case-insensitive: "Tests/TEST_CART.PY" is the same inode as the real suite."""

    def setUp(self) -> None:
        super().setUp()
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests" / "test_cart.py").write_text("original\n", encoding="utf-8")
        self.allowed = frozenset(relative_files(self.tmp))

    def test_is_test_path_is_case_insensitive(self):
        for candidate in ["tests/test_cart.py", "Tests/TEST_CART.PY", "TESTS/Test_Cart.py"]:
            with self.subTest(path=candidate):
                self.assertTrue(is_test_path(self.tmp, self.tmp / candidate))

    def test_a_case_variant_write_cannot_reach_the_test_file(self):
        out = WriteFileTool(root=self.tmp, allowed=self.allowed).invoke(
            {"path": "Tests/TEST_CART.PY", "content": PASSING_SUITE}
        )
        self.assertIn("Refused", out)
        self.assertEqual((self.tmp / "tests" / "test_cart.py").read_text(), "original\n")


class TestRunnerShadowing(TempDirTestCase):
    """`python -m unittest` puts the workspace first on sys.path."""

    def setUp(self) -> None:
        super().setUp()
        (self.tmp / "cart.py").write_text("def total():\n    return 0\n", encoding="utf-8")
        self.allowed = frozenset(relative_files(self.tmp))

    def test_the_stdlib_test_runner_cannot_be_shadowed(self):
        out = WriteFileTool(root=self.tmp, allowed=self.allowed).invoke(
            {"path": "unittest.py", "content": "import sys\n\nsys.exit(0)\n"}
        )
        self.assertIn("Refused", out)
        self.assertFalse((self.tmp / "unittest.py").exists())

    def test_shadowing_really_would_have_forged_the_oracle(self):
        """Proves the check above is load-bearing rather than guarding nothing."""
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.tmp / "tests" / "test_x.py").write_text(
            "import unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_red(self):\n        self.assertEqual(1, 2)\n",
            encoding="utf-8",
        )
        argv = [sys.executable, "-m", "unittest", "discover", "-q"]
        red = subprocess.run(argv, cwd=self.tmp, capture_output=True, check=False)
        self.assertNotEqual(red.returncode, 0, "the suite must start red")

        (self.tmp / "unittest.py").write_text("import sys\n\nsys.exit(0)\n", encoding="utf-8")
        forged = subprocess.run(argv, cwd=self.tmp, capture_output=True, check=False)
        self.assertEqual(forged.returncode, 0, "shadowing forges a pass — hence the allow-list")

    def test_a_startup_hook_cannot_be_planted(self):
        """A .pth file under a workspace-relative site-packages runs code before any test."""
        out = WriteFileTool(root=self.tmp, allowed=self.allowed).invoke(
            {"path": ".local/lib/python3.12/site-packages/evil.pth", "content": "import os\n"}
        )
        self.assertIn("Refused", out)


class TestAllowList(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        (self.tmp / "cart.py").write_text("x = 1\n", encoding="utf-8")
        self.allowed = frozenset(relative_files(self.tmp))

    def test_an_existing_file_is_still_writable(self):
        """The fix must not stop the agent doing its actual job."""
        out = WriteFileTool(root=self.tmp, allowed=self.allowed).invoke(
            {"path": "cart.py", "content": "x = 2\n"}
        )
        self.assertIn("Wrote", out)
        self.assertEqual((self.tmp / "cart.py").read_text(), "x = 2\n")

    def test_no_allow_list_means_no_check(self):
        out = WriteFileTool(root=self.tmp).invoke({"path": "new.py", "content": "x = 1\n"})
        self.assertIn("Wrote", out)


class TestEndToEnd(TempDirTestCase):
    def test_the_agent_still_solves_a_real_fixture_with_the_allow_list_active(self):
        """Parity check: the hardening must not change the agent's legitimate behaviour."""
        fixed = (
            "from shopcart.pricing import with_tax\n\n\n"
            "def subtotal(prices: list[float]) -> float:\n    return sum(prices)\n\n\n"
            "def total_with_tax(prices: list[float]) -> float:\n"
            "    return with_tax(subtotal(prices))\n"
        )
        llm = FakeChatModel(
            replies=[
                assistant_tool_call("write_file", {"path": "shopcart/cart.py", "content": fixed}),
                assistant_tool_call("run_tests", {}),
                assistant_text("done"),
            ]
        )
        self.assertTrue(solve_task(SHOPCART, llm=llm, max_steps=3).solved)

    def test_the_agent_cannot_forge_a_pass_through_the_real_wiring(self):
        llm = FakeChatModel(
            replies=[
                assistant_tool_call(
                    "write_file", {"path": "unittest.py", "content": "import sys\nsys.exit(0)\n"}
                ),
                assistant_tool_call("run_tests", {}),
                assistant_text("done"),
            ]
        )
        self.assertFalse(solve_task(SHOPCART, llm=llm, max_steps=3).solved)


@unittest.skipUnless(PREBUILT_AVAILABLE, "needs the prebuilt extra: uv sync --extra prebuilt")
class PrebuiltTestCase(TempDirTestCase):
    """The same red one-file project tests/test_graph.py uses."""

    def setUp(self) -> None:
        super().setUp()
        (self.tmp / "cart.py").write_text(BUGGY, encoding="utf-8")
        (self.tmp / "test_cart.py").write_text(SUITE, encoding="utf-8")
        self.tools = [
            ListFilesTool(root=self.tmp),
            ReadFileTool(root=self.tmp),
            WriteFileTool(root=self.tmp),
            RunTestsTool(
                root=self.tmp,
                command=(sys.executable, "-m", "unittest", "discover", "-q"),
                backend=SubprocessBackend(),
                timeout_s=30,
            ),
        ]

    def run_with(self, replies, max_steps=None):
        replies = list(replies)
        llm = FakeChatModel(replies=replies)
        app = build_prebuilt_agent(
            llm, self.tools, max_steps=len(replies) if max_steps is None else max_steps
        )
        final = app.invoke({"messages": [("user", "The tests fail. Fix the bug.")]})
        return final, llm


class TestWhatTheFrameworkGives(PrebuiltTestCase):
    def test_it_solves_the_task(self):
        """The tool-calling loop itself needs none of graph.py."""
        final, llm = self.run_with(
            [
                assistant_tool_call("run_tests", {}),
                assistant_tool_call("read_file", {"path": "cart.py"}),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("Fixed the off-by-one."),
            ]
        )
        self.assertTrue(prebuilt_solved(final))
        self.assertEqual((self.tmp / "cart.py").read_text(), FIXED)

    def test_the_step_budget_is_model_calls_not_node_executions(self):
        """ModelCallLimitMiddleware counts the same thing AgentState.step counts."""
        _, llm = self.run_with(
            [assistant_tool_call("list_files", {}, call_id=f"c{i}") for i in range(10)],
            max_steps=3,
        )
        self.assertEqual(llm.index, 3, "the run must stop after three model calls")

    def test_prose_while_the_suite_is_red_does_not_end_the_run(self):
        """after_model + jump_to="model" is what makes the verified stop expressible."""
        _, llm = self.run_with(
            [assistant_text("I am confident it is fine."), assistant_text("still fine")],
            max_steps=2,
        )
        self.assertEqual(llm.index, 2, "the model was sent back rather than believed")
        self.assertTrue(
            any("tests have not passed" in str(m.content) for m in llm.calls[-1]),
            "the nudge reached the model",
        )

    def test_an_identical_repeated_call_is_not_executed_again(self):
        """wrap_tool_call can answer a call without running it — that is the whole guard."""
        final, _ = self.run_with(
            [
                assistant_tool_call("list_files", {}, call_id="c1"),
                assistant_tool_call("list_files", {}, call_id="c2"),
                assistant_text("done"),
            ],
            max_steps=3,
        )
        answers = [str(m.content) for m in final["messages"] if getattr(m, "tool_call_id", None)]
        self.assertTrue(any("already called" in a for a in answers))


class TestMiddlewareOrderIsLoadBearing(PrebuiltTestCase):
    def test_the_step_budget_only_applies_if_the_limit_is_ordered_last(self):
        """The finding that justifies this file existing.

        `after_model` hooks run in reverse list order and routing obeys whichever `jump_to`
        reached the state last, so a budget middleware placed before one that jumps back to the
        model is silently overruled. Nothing warns you; the agent runs until something else
        stops it — here, the scripted fake running out of replies.
        """
        from langchain.agents import create_agent
        from langchain.agents.middleware import ModelCallLimitMiddleware

        from agentlang.agent.graph import system_prompt
        from agentlang.agent.prebuilt import LoopGuard, VerifiedStop

        def calls_made(limit_first: bool) -> int:
            llm = FakeChatModel(replies=[assistant_text(f"no {i}") for i in range(12)])
            limit = ModelCallLimitMiddleware(run_limit=3, exit_behavior="end")
            order = [limit, LoopGuard(), VerifiedStop()]
            if not limit_first:
                order = [LoopGuard(), VerifiedStop(), limit]
            app = create_agent(
                model=llm,
                tools=self.tools,
                system_prompt=system_prompt(self.tools),
                middleware=order,
            )
            try:
                app.invoke({"messages": [("user", "fix")]})
            except AssertionError:
                pass  # the fake exhausted its script — the symptom of an ignored budget
            return llm.index

        self.assertEqual(calls_made(limit_first=False), 3, "limit last: budget respected")
        self.assertGreater(calls_made(limit_first=True), 3, "limit first: budget ignored")


class TestWhatIsStillMissing(PrebuiltTestCase):
    """The gaps that keep agent/graph.py from collapsing into a constructor call."""

    def test_the_guards_counters_leak_between_runs_of_one_agent(self):
        """The consequence of keeping guard state on the middleware instead of in the state.

        Replaces an earlier assertion that `guard_hits` is absent from the state, which could
        only ever fail if the framework adopted our key names. This asserts the behaviour that
        absence actually causes.
        """
        llm = FakeChatModel(
            replies=[
                assistant_tool_call("list_files", {}, call_id="a1"),
                assistant_text("done"),
                assistant_tool_call("list_files", {}, call_id="b1"),
                assistant_text("done again"),
            ]
        )
        app = build_prebuilt_agent(llm, self.tools, max_steps=2)
        app.invoke({"messages": [("user", "task one")]})
        second = app.invoke({"messages": [("user", "a brand new task")]})

        answers = [str(m.content) for m in second["messages"] if getattr(m, "tool_call_id", None)]
        self.assertTrue(
            any("already called" in a for a in answers),
            "run 2's opening call is refused as a repeat of run 1's last one",
        )

    def test_a_stuck_model_runs_to_the_budget_instead_of_being_abandoned(self):
        """The guard can answer a repeated call but cannot end the run.

        agent/graph.py abandons after MAX_GUARD_HITS repeats; here the model keeps its whole
        budget, which is the difference between a seam and a policy.
        """
        _, llm = self.run_with(
            [assistant_tool_call("list_files", {}, call_id=f"c{i}") for i in range(9)],
            max_steps=9,
        )
        self.assertEqual(llm.index, 9, "nine identical calls cost nine model turns")

    def test_a_checkpointer_is_refused_rather_than_silently_mis_reporting(self):
        """The verdict is recomputed from artifacts a checkpoint round-trip turns into dicts.

        Measured before this guard existed: a live run reported solved, the same thread resumed
        reported unsolved, and VerifiedStop then nudged a green suite until the budget ran out.
        Refusing names the limitation where someone would hit it.
        """
        from langgraph.checkpoint.memory import InMemorySaver

        with self.assertRaises(NotImplementedError) as ctx:
            build_prebuilt_agent(
                FakeChatModel(replies=[]), self.tools, checkpointer=InMemorySaver()
            )
        self.assertIn("nowhere to keep the verdict", str(ctx.exception))

    def test_the_verdict_has_to_be_recomputed_from_the_messages(self):
        """`create_agent` carries its own state, so there is no tests_passed to read.

        The `assertNotIn` half is weak on its own — it only fails if the framework adopts our
        key name. The half that carries weight is that `prebuilt_solved` has to re-fold the
        whole history to answer a question `agent/graph.py` reads from one bool.
        """
        final, _ = self.run_with(
            [
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("done"),
            ]
        )
        self.assertNotIn("tests_passed", final)
        self.assertTrue(prebuilt_solved(final))
        # And the artifacts it depends on are live objects, not the dicts a checkpoint returns.
        artifacts = [
            m.artifact for m in final["messages"] if getattr(m, "artifact", None) is not None
        ]
        self.assertTrue(artifacts)
        self.assertFalse([a for a in artifacts if isinstance(a, dict)])


# -------------------------------------------------------------------------------------------
# The scripted fake and the real client: what the model layer promises the graph.
# -------------------------------------------------------------------------------------------


class TestReplyBuilders(unittest.TestCase):
    def test_text_reply_has_no_tool_calls(self):
        reply = assistant_text("done")
        self.assertEqual(reply.tool_calls, [])
        self.assertEqual(reply.text, "done")

    def test_a_tool_call_carries_parsed_arguments_and_an_id(self):
        reply = assistant_tool_call("read_file", {"path": "a.py"}, call_id="c9")
        self.assertEqual(len(reply.tool_calls), 1)
        call = reply.tool_calls[0]
        self.assertEqual(call["name"], "read_file")
        self.assertEqual(call["args"], {"path": "a.py"})
        self.assertEqual(call["id"], "c9")

    def test_usage_is_reported_so_the_graph_has_something_to_add_up(self):
        reply = assistant_tool_call("run_tests", {}, prompt_tokens=556)
        self.assertEqual((reply.usage_metadata or {})["input_tokens"], 556)

    def test_several_calls_get_distinct_default_ids(self):
        reply = assistant_tool_calls([("run_tests", {}), ("list_files", {})])
        self.assertEqual([c["id"] for c in reply.tool_calls], ["call_1", "call_2"])

    def test_duplicate_ids_are_rejected(self):
        """The whole point of an id is to tell two calls apart."""
        with self.assertRaises(AssertionError):
            assistant_tool_calls([("run_tests", {}), ("list_files", {})], call_ids=("c1", "c1"))

    def test_mismatched_id_count_is_rejected(self):
        with self.assertRaises(AssertionError):
            assistant_tool_calls([("run_tests", {})], call_ids=("c1", "c2"))


class TestFakeChatModel(unittest.TestCase):
    def test_replies_are_returned_in_order(self):
        llm = FakeChatModel(replies=[assistant_text("one"), assistant_text("two")])
        self.assertEqual(llm.invoke([HumanMessage("go")]).text, "one")
        self.assertEqual(llm.invoke([HumanMessage("go")]).text, "two")

    def test_each_history_is_snapshotted(self):
        llm = FakeChatModel(replies=[assistant_text("a"), assistant_text("b")])
        llm.invoke([HumanMessage("1")])
        llm.invoke([HumanMessage("1"), HumanMessage("2")])
        self.assertEqual([len(c) for c in llm.calls], [1, 2])

    def test_running_off_the_end_of_the_script_is_a_diagnosis(self):
        llm = FakeChatModel(replies=[assistant_text("only one")])
        llm.invoke([HumanMessage("go")])
        with self.assertRaises(AssertionError) as ctx:
            llm.invoke([HumanMessage("go")])
        self.assertIn("more turns than the test scripted", str(ctx.exception))

    def test_bind_tools_keeps_the_same_object_so_state_stays_observable(self):
        llm = FakeChatModel(replies=[assistant_text("x")])
        self.assertIs(llm.bind_tools([]), llm)


class TestLLMConfig(unittest.TestCase):
    def test_defaults_point_at_local_ollama(self):
        config = LLMConfig()
        self.assertEqual(config.base_url, DEFAULT_BASE_URL)
        self.assertEqual(config.model, DEFAULT_MODEL)

    def test_the_api_url_tolerates_a_leftover_v1_suffix(self):
        """An environment set up for the old ChatOpenAI client must not produce /v1/api/ps."""
        self.assertEqual(
            LLMConfig(base_url="http://localhost:11434/v1").api_url,
            "http://localhost:11434",
        )
        self.assertEqual(
            LLMConfig(base_url="http://localhost:11434/").api_url, "http://localhost:11434"
        )

    def test_env_overrides(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"MELLUM_BASE_URL": "http://x/v1", "MELLUM_MODEL": "m"}):
            config = LLMConfig.from_env()
        self.assertEqual(config.base_url, "http://x/v1")
        self.assertEqual(config.model, "m")

    def test_the_config_is_frozen(self):
        with self.assertRaises(Exception):
            LLMConfig().model = "other"  # type: ignore[misc]


class TestMakeChatModel(unittest.TestCase):
    def test_the_config_reaches_the_client(self):
        model = make_chat_model(LLMConfig(base_url="http://x", model="m", temperature=0.1))
        self.assertEqual(model.base_url, "http://x")
        self.assertEqual(model.model, "m")
        self.assertEqual(model.temperature, 0.1)

    def test_the_reply_cap_reaches_the_server(self):
        """A regression guard, not a style preference.

        This is the setting the previous client could not deliver. ChatOpenAI's `max_tokens` is
        aliased to `max_completion_tokens`, which Ollama's /v1 endpoint ignores — measured,
        asking for 8 tokens: 692 were generated. `num_predict` is Ollama's own name for it and
        is honoured, and it is the ceiling on how large a file write_file can emit in one turn.
        """
        self.assertEqual(make_chat_model(LLMConfig(max_tokens=1024)).num_predict, 1024)

    def test_the_context_window_reaches_the_server(self):
        """Also silently dropped by /v1, which is why the Modelfile had to carry it."""
        self.assertEqual(make_chat_model(LLMConfig(num_ctx=16384)).num_ctx, 16384)

    def test_a_leftover_v1_base_url_is_normalised(self):
        """Ollama's native API is not under /v1; an old MELLUM_BASE_URL must still work."""
        model = make_chat_model(LLMConfig(base_url="http://localhost:11434/v1"))
        self.assertEqual(model.base_url, "http://localhost:11434")

    def test_no_credential_is_read_from_the_environment(self):
        """Pointing MELLUM_BASE_URL elsewhere must not leak a real OpenAI credential."""
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-real-secret"}):
            model = make_chat_model(LLMConfig())
        self.assertNotIn("sk-real-secret", repr(model))

    def test_env_configuration_is_picked_up_when_no_config_is_passed(self):
        with mock.patch.dict(os.environ, {"MELLUM_MODEL": "other-model"}):
            self.assertEqual(make_chat_model().model, "other-model")


# -------------------------------------------------------------------------------------------
# The sandbox: where the tests actually execute, and how little it is allowed to do.
# -------------------------------------------------------------------------------------------


class TestSubprocessBackend(TempDirTestCase):
    def _write_test(self, body: str, name: str = "test_x.py") -> None:
        (self.tmp / name).write_text(body, encoding="utf-8")

    def test_a_passing_suite_reports_passed(self):
        self._write_test(
            "import unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_ok(self):\n        self.assertTrue(True)\n"
        )
        self.assertTrue(SubprocessBackend().run(self.tmp, UNITTEST_CMD, timeout_s=30).passed)

    def test_a_failing_suite_reports_not_passed_and_keeps_the_output(self):
        self._write_test(
            "import unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_no(self):\n        self.assertEqual(1, 2)\n"
        )
        result = SubprocessBackend().run(self.tmp, UNITTEST_CMD, timeout_s=30)
        self.assertFalse(result.passed)
        self.assertIn("test_no", result.output)

    def test_discovering_no_tests_is_not_a_pass(self):
        """unittest exits 5 here. If that read as success, every task would report SOLVED."""
        result = SubprocessBackend().run(self.tmp, UNITTEST_CMD, timeout_s=30)
        self.assertFalse(result.passed)

    def test_output_is_truncated_with_a_marker(self):
        self._write_test(
            "import unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_loud(self):\n        print('x' * 50000)\n        self.assertEqual(1, 2)\n"
        )
        result = SubprocessBackend(max_output_chars=500).run(self.tmp, UNITTEST_CMD, timeout_s=30)
        self.assertLess(len(result.output), 800)
        self.assertIn("truncated", result.output)

    def test_the_child_environment_is_stripped(self):
        """No API keys, no PYTHONPATH that could shadow the task's own modules."""
        self._write_test(
            "import os, unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_env(self):\n"
            "        self.assertIsNone(os.environ.get('AGENTFIX_SECRET'))\n"
        )
        with mock.patch.dict(os.environ, {"AGENTFIX_SECRET": "leaked"}):
            self.assertTrue(SubprocessBackend().run(self.tmp, UNITTEST_CMD, timeout_s=30).passed)


class TestBackendSelection(unittest.TestCase):
    def test_defaults_to_subprocess(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsInstance(get_backend(), SubprocessBackend)

    def test_the_environment_variable_selects_docker(self):
        with mock.patch.dict(os.environ, {"AGENTFIX_SANDBOX": "docker"}):
            self.assertIsInstance(get_backend(), DockerBackend)

    def test_an_explicit_argument_wins_over_the_environment(self):
        with mock.patch.dict(os.environ, {"AGENTFIX_SANDBOX": "docker"}):
            self.assertIsInstance(get_backend("subprocess"), SubprocessBackend)

    def test_a_typo_fails_loudly_rather_than_silently_weakening_isolation(self):
        with self.assertRaises(ValueError):
            get_backend("dokcer")


class TestDockerArgv(unittest.TestCase):
    def setUp(self) -> None:
        self.argv = DockerBackend().build_argv(
            Path("/tmp/ws"), ("/usr/bin/python3", "-m", "unittest", "discover", "-q"), name="c1"
        )

    def _pair(self, flag: str) -> str:
        return self.argv[self.argv.index(flag) + 1]

    def test_the_container_is_removed_and_named(self):
        self.assertIn("--rm", self.argv)
        self.assertEqual(self._pair("--name"), "c1")

    def test_there_is_no_network(self):
        """The real difference from the subprocess backend."""
        self.assertEqual(self._pair("--network"), "none")

    def test_resources_are_capped(self):
        self.assertEqual(self._pair("--memory"), "512m")
        self.assertEqual(self._pair("--pids-limit"), "128")
        self.assertEqual(self._pair("--cpus"), "1")

    def test_privileges_are_dropped(self):
        self.assertEqual(self._pair("--user"), "runner")
        self.assertEqual(self._pair("--cap-drop"), "ALL")
        self.assertEqual(self._pair("--security-opt"), "no-new-privileges")

    def test_the_filesystem_is_immutable_apart_from_tmp(self):
        self.assertIn("--read-only", self.argv)
        self.assertEqual(self._pair("--tmpfs"), "/tmp")

    def test_the_workspace_is_mounted_read_only(self):
        """The file tools write on the host, so the container never needs write access."""
        self.assertEqual(self._pair("--volume"), "/tmp/ws:/work:ro")
        self.assertEqual(self._pair("--workdir"), "/work")

    def test_bytecode_writing_is_disabled(self):
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", self.argv)

    def test_the_host_interpreter_path_is_replaced_by_the_container_python(self):
        self.assertEqual(
            self.argv[self.argv.index(DEFAULT_IMAGE) + 1 :],
            ["python", "-m", "unittest", "discover", "-q"],
        )

    def test_container_names_are_unique_per_run(self):
        names = {DockerBackend()._container_name() for _ in range(50)}
        self.assertEqual(len(names), 50)


# -------------------------------------------------------------------------------------------
# The tools: the agent's only way to touch the world, and its only containment.
# -------------------------------------------------------------------------------------------


class TestResolveInRoot(TempDirTestCase):
    def test_a_plain_relative_path_resolves_inside(self):
        self.assertEqual(resolve_in_root(self.tmp, "a.py"), (self.tmp / "a.py").resolve())

    def test_the_root_itself_is_allowed(self):
        self.assertEqual(resolve_in_root(self.tmp, "."), self.tmp.resolve())

    def test_dot_dot_escape_is_refused(self):
        with self.assertRaises(PathEscapeError):
            resolve_in_root(self.tmp, "../../../../etc/passwd")

    def test_an_absolute_path_outside_is_refused(self):
        with self.assertRaises(PathEscapeError):
            resolve_in_root(self.tmp, "/etc/passwd")

    def test_a_symlink_pointing_out_is_refused(self):
        """String comparison would miss this; `.resolve()` is what catches it."""
        outside = self.tmp.parent / "outside_target"
        outside.mkdir(exist_ok=True)
        (self.tmp / "link").symlink_to(outside)
        with self.assertRaises(PathEscapeError):
            resolve_in_root(self.tmp, "link/secret.py")


class TestIsTestPath(TempDirTestCase):
    def test_a_file_in_a_tests_directory_is_protected(self):
        self.assertTrue(is_test_path(self.tmp, self.tmp / "tests" / "test_cart.py"))

    def test_a_test_prefixed_file_at_the_root_is_protected(self):
        self.assertTrue(is_test_path(self.tmp, self.tmp / "test_candidate.py"))

    def test_ordinary_source_is_not_protected(self):
        self.assertFalse(is_test_path(self.tmp, self.tmp / "shopcart" / "cart.py"))

    def test_a_path_outside_the_root_is_not_called_a_test(self):
        self.assertFalse(is_test_path(self.tmp, Path("/etc/passwd")))


class TestListFiles(TempDirTestCase):
    def test_lists_python_files_relative_and_sorted(self):
        (self.tmp / "pkg").mkdir()
        (self.tmp / "pkg" / "b.py").write_text("")
        (self.tmp / "a.py").write_text("")
        (self.tmp / "notes.txt").write_text("ignored")
        out = ListFilesTool(root=self.tmp).invoke({})
        self.assertEqual(out.splitlines(), ["a.py", "pkg/b.py"])

    def test_ignores_noise_directories(self):
        (self.tmp / "__pycache__").mkdir()
        (self.tmp / "__pycache__" / "x.py").write_text("")
        self.assertIn("no Python files", ListFilesTool(root=self.tmp).invoke({}))

    def test_an_empty_project_is_an_observation_not_an_error(self):
        self.assertIn("no Python files", ListFilesTool(root=self.tmp).invoke({}))


class TestReadFile(TempDirTestCase):
    def test_reads_a_file(self):
        (self.tmp / "a.py").write_text("x = 1\n")
        self.assertEqual(ReadFileTool(root=self.tmp).invoke({"path": "a.py"}), "x = 1\n")

    def test_a_missing_file_lists_what_does_exist(self):
        (self.tmp / "real.py").write_text("")
        out = ReadFileTool(root=self.tmp).invoke({"path": "typo.py"})
        self.assertIn("No such file", out)
        self.assertIn("real.py", out)

    def test_an_escape_is_refused_as_an_observation_not_an_exception(self):
        out = ReadFileTool(root=self.tmp).invoke({"path": "../../etc/passwd"})
        self.assertIn("Refused", out)

    def test_a_large_file_is_truncated_with_a_visible_marker(self):
        (self.tmp / "big.py").write_text("# " + "x" * 9000)
        out = ReadFileTool(root=self.tmp).invoke({"path": "big.py"})
        self.assertTrue(out.endswith(TRUNCATION_MARKER))


class TestWriteFile(TempDirTestCase):
    def test_writes_and_reports_the_size(self):
        out = WriteFileTool(root=self.tmp).invoke({"path": "a.py", "content": "x = 1\n"})
        self.assertEqual((self.tmp / "a.py").read_text(), "x = 1\n")
        self.assertIn("6 characters", out)

    def test_refuses_to_rewrite_the_test_suite(self):
        """The tests are the specification. Without this an agent can fake a green run."""
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests" / "test_cart.py").write_text("original")
        out = WriteFileTool(root=self.tmp).invoke(
            {"path": "tests/test_cart.py", "content": "# deleted the assertion"}
        )
        self.assertIn("Refused", out)
        self.assertEqual((self.tmp / "tests" / "test_cart.py").read_text(), "original")

    def test_a_syntax_error_is_reported_and_nothing_is_written(self):
        out = WriteFileTool(root=self.tmp).invoke({"path": "a.py", "content": "def f(:\n"})
        self.assertIn("syntax error", out)
        self.assertFalse((self.tmp / "a.py").exists())

    def test_an_escape_is_refused(self):
        out = WriteFileTool(root=self.tmp).invoke({"path": "../evil.py", "content": "x = 1"})
        self.assertIn("Refused", out)
        self.assertFalse((self.tmp.parent / "evil.py").exists())

    def _message(self, tool: WriteFileTool, path: str, content: str):
        call = {"name": "write_file", "args": {"path": path, "content": content}}
        return tool.invoke({**call, "id": "c1", "type": "tool_call"})

    def test_a_successful_write_reports_the_workspace_changed(self):
        """That artifact is what invalidates the last test result, in the graph's state."""
        message = self._message(WriteFileTool(root=self.tmp), "a.py", "x = 1\n")
        self.assertIsInstance(message.artifact, WorkspaceChanged)
        self.assertEqual(message.artifact.path, "a.py")

    def test_a_rejected_write_reports_no_change(self):
        """A refusal changed nothing, so it must not invalidate a green test result."""
        tool = WriteFileTool(root=self.tmp)
        self.assertIsNone(self._message(tool, "b.py", "def f(:\n").artifact)
        self.assertIsNone(self._message(tool, "../evil.py", "x = 1\n").artifact)
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests" / "test_x.py").write_text("original")
        self.assertIsNone(self._message(tool, "tests/test_x.py", "x = 1\n").artifact)

    def test_creates_missing_parent_directories(self):
        WriteFileTool(root=self.tmp).invoke({"path": "deep/nested/a.py", "content": "x = 1\n"})
        self.assertTrue((self.tmp / "deep" / "nested" / "a.py").is_file())


class TestRunTestsTool(TempDirTestCase):
    def _tool(self, backend: FakeBackend) -> RunTestsTool:
        return RunTestsTool(root=self.tmp, command=PYTHON_UNITTEST, backend=backend)

    def test_declares_a_schema_the_model_can_use(self):
        tool = self._tool(FakeBackend())
        self.assertEqual(tool.name, "run_tests")
        self.assertTrue(tool.description, "the model chooses tools by their description")
        self.assertEqual(tool.args_schema.model_json_schema()["type"], "object")

    def test_a_failing_suite_is_a_normal_observation_not_a_tool_error(self):
        """Failing tests are the information the agent needs, not a malfunction."""
        out = self._tool(FakeBackend(ExecResult(False, "1 failed", 0.1))).invoke({})
        self.assertIn("Tests failed.", out)
        self.assertIn("1 failed", out)

    def test_the_verdict_is_the_first_line(self):
        """unittest buries its verdict at the end; a 12B model reads the top of the output."""
        out = self._tool(FakeBackend(ExecResult(True, "Ran 2 tests\nOK", 0.1))).invoke({})
        self.assertTrue(out.startswith("All tests passed."))

    def test_the_exec_result_comes_back_as_the_message_artifact(self):
        """The graph reads this, not the prose: a stop condition must not parse text."""
        tool = self._tool(FakeBackend(ExecResult(True, "OK", 0.1)))
        message = tool.invoke({"name": "run_tests", "args": {}, "id": "c1", "type": "tool_call"})
        self.assertIsInstance(message.artifact, ExecResult)
        self.assertTrue(message.artifact.passed)
        self.assertIn("All tests passed.", str(message.content))

    def test_the_tool_keeps_no_state_of_its_own(self):
        """The verdict lives in the graph state, which is what makes a run resumable."""
        tool = self._tool(FakeBackend(ExecResult(True, "OK", 0.1)))
        tool.invoke({})
        self.assertFalse(hasattr(tool, "last_result"))

    def test_the_backend_is_asked_to_run_the_task_command_in_the_workspace(self):
        backend = FakeBackend()
        self._tool(backend).invoke({})
        workspace, command, _ = backend.calls[0]
        self.assertEqual(workspace, self.tmp)
        self.assertEqual(command, PYTHON_UNITTEST)


if __name__ == "__main__":
    unittest.main()
