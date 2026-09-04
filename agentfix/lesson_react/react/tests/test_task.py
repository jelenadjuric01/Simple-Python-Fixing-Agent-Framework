"""The ReAct edition, tested end to end — plus Exercise 1, the thinking guard.

Everything here drives the REAL graph and the REAL tools in a real temp directory. Only the
model is scripted, so the whole file runs offline: no Ollama, no network, no Docker daemon.
`llm/fake.py` is a real `BaseChatModel` handed a list of replies, and it puts reasoning in
exactly the field the real client puts it in (`additional_kwargs["reasoning_content"]`) — a fake
that put it anywhere else would let a broken agent pass every test below.

Read in the order the sections appear, which is the order the pieces depend on each other:

  1. Exercise 1        the thinking guard you write — what a turn that changed nothing costs
  2. the agent         the graph, the budget, the stop condition, the loop guard, checkpointing
  3. reasoning         what makes this the ReAct edition rather than the previous one
  4. the state         the reducers that combine one node's update with what is already there
  5. the trace         the observability, and the one file the new model actively broke
  6. the model layer   the scripted fake, and the real client's configuration
  7. the sandbox       where the agent's test runs actually execute
  8. the tools         the agent's only way to touch the world
  9. tasks and wiring  the disposable workspace, and the pieces assembled
 10. the oracle        the agent must not be able to pass the tests without fixing the bug
 11. the fixtures      every shipped task must start red, for the reason it claims

The CLI, the doctor, the eval harness and the prebuilt-agent comparison are not part of the
agent itself and are not tested here.

Exercise 1 owns the thinking-guard and nudge assertions; section 3 covers the rest of reasoning
rather than repeating them.
"""

from __future__ import annotations

import inspect
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, Generation, LLMResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import ToolNode

from agentgraph.agent.graph import (
    MAX_GUARD_HITS,
    MAX_IDLE_TURNS,
    NUDGE,
    NUDGE_AFTER_THINKING,
    UNREADABLE_REPLY,
    acted,
    build_graph,
    call_signature,
    completion_tokens_of,
    run_agent,
    system_prompt,
)
from agentgraph.agent.state import initial_state, keep_larger
from agentgraph.agent.trace import (
    DETAIL_CLIP,
    TraceEvent,
    Tracer,
    describe,
    prompt_tokens_of,
    reasoning_of,
)
import agentgraph.config
from agentgraph.config import DEFAULT_BASE_URL, DEFAULT_MODEL, LLMConfig, REPO_ROOT
from agentgraph.llm.client import make_chat_model
from agentgraph.llm.fake import (
    FakeChatModel,
    assistant_text,
    assistant_thinking,
    assistant_tool_call,
    assistant_tool_calls,
    unreadable_reply,
)
from agentgraph.runner import solve_task
from agentgraph.sandbox.base import ExecResult, get_backend
from agentgraph.sandbox.docker_backend import DEFAULT_IMAGE, DockerBackend
from agentgraph.sandbox.subprocess_backend import SubprocessBackend
from agentgraph.tasks.loader import DEFAULT_PROMPT, Task, load_task, workspace
from agentgraph.tools.base import TRUNCATION_MARKER, WorkspaceChanged
from agentgraph.tools.fs import (
    ListFilesTool,
    PathEscapeError,
    ReadFileTool,
    WriteFileTool,
    is_test_path,
    relative_files,
    resolve_in_root,
)
from agentgraph.tools.tests_tool import RunTestsTool

# -------------------------------------------------------------------------------------------
# Support: everything these tests need that is not the agent
# -------------------------------------------------------------------------------------------

# Anchored to this file rather than to the working directory, because a lesson's tests are run
# from more than one place: the IDE's Check button, `python run.py agentgraph unittest
# tests.test_task`, and a plain `python -m unittest` in the task directory.
#
# Deliberately not `agentgraph.config.REPO_ROOT`, even though it now resolves to this same
# directory. That constant is code under test here — `TestRepoRoot` checks it points at the
# fixtures — and a test that took its expected path from the thing it is checking would agree
# with any answer.
TASK_ROOT = Path(__file__).resolve().parents[1]
SHOPCART = TASK_ROOT / "tasks" / "workshop" / "01-shopcart"
WORKSHOP_TASKS = TASK_ROOT / "tasks" / "workshop"
TASK_DIRS = sorted(path.parent for path in WORKSHOP_TASKS.glob("*/task.json"))

PYTHON_UNITTEST = ("python", "-m", "unittest", "discover", "-q")
UNITTEST_CMD = (sys.executable, "-m", "unittest", "discover", "-q")


class TempDirTestCase(unittest.TestCase):
    """A disposable directory in `self.tmp`, gone again however the test ends."""

    def setUp(self) -> None:
        super().setUp()
        # `.resolve()` is not decoration. On macOS this hands back /var/folders/..., a symlink
        # to /private/var/folders/..., while every check in tools/fs.py compares against
        # `.resolve()` output — without it those comparisons silently disagree.
        self.tmp = Path(tempfile.mkdtemp(prefix="agentgraph_test_")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class FakeBackend:
    """An execution backend that returns a canned result and records what it was asked to run.

    Satisfies the `ExecutionBackend` protocol structurally, without inheriting from it — which
    is the whole point of Protocol, and why `run_tests` can be tested with no subprocess.
    """

    def __init__(self, result: ExecResult | None = None) -> None:
        self.result = result or ExecResult(passed=False, output="1 failed", duration_s=0.01)
        self.calls: list[tuple[Path, tuple[str, ...], int]] = []

    def run(self, workspace: Path, command: tuple[str, ...], timeout_s: int = 10) -> ExecResult:
        self.calls.append((workspace, command, timeout_s))
        return self.result


def make_task(root: Path, task_id: str = "t", prompt: str = "Fix it.") -> Task:
    """A Task for an already-materialised project, with no task.json on disk.

    `template_dir` points at `root` itself rather than a pristine `repo/` copy: the tools in
    these tests are bound to the directory directly instead of going through `workspace()`, so
    there is nothing to copy from.
    """
    return Task(
        task_id=task_id,
        root=root,
        template_dir=root,
        test_command=PYTHON_UNITTEST,
        expected_failures=(),
        prompt=prompt,
    )


# The one-file project almost every test below runs against: red on arrival, green after the
# agent's write, and really executed either way.
BUGGY = "def total(prices):\n    return sum(prices) - 1\n"
FIXED = "def total(prices):\n    return sum(prices)\n"
SUITE = (
    "import unittest\n\n"
    "from cart import total\n\n\n"
    "class TestCart(unittest.TestCase):\n"
    "    def test_total(self):\n"
    "        self.assertEqual(total([1, 2]), 3)\n"
)

# A plausible thought, used wherever a turn needs to have reasoned about something.
THOUGHT = "The test expects 3 and got 2, so the subtraction in total() is wrong."

# The reference fix for the 01-shopcart fixture, for the tests that go through the real wiring.
SHOPCART_FIXED = (
    "from shopcart.pricing import with_tax\n\n\n"
    "def subtotal(prices: list[float]) -> float:\n    return sum(prices)\n\n\n"
    "def total_with_tax(prices: list[float]) -> float:\n"
    "    return with_tax(subtotal(prices))\n"
)

# A suite that passes unconditionally — what an agent would plant if it could rewrite the tests.
PASSING_SUITE = (
    "import unittest\n\n\nclass TestCart(unittest.TestCase):\n"
    "    def test_ok(self):\n        self.assertTrue(True)\n"
)


class GraphTestCase(TempDirTestCase):
    """A real one-file project that starts red, plus the four tools bound to it."""

    def setUp(self) -> None:
        super().setUp()
        (self.tmp / "cart.py").write_text(BUGGY, encoding="utf-8")
        (self.tmp / "test_cart.py").write_text(SUITE, encoding="utf-8")
        self.task = make_task(self.tmp)
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
        # The script is the budget by default. A reply that acted on nothing is nudged rather
        # than accepted, and the nudge costs a turn — so a script of N replies needs exactly N
        # steps, or the fake runs off the end of its own screenplay.
        replies = list(replies)
        max_steps = len(replies) if max_steps is None else max_steps
        llm = FakeChatModel(replies=replies)
        result = run_agent(self.task, llm, self.tools, max_steps=max_steps, tracer=tracer)
        return result, llm

    def nudges_sent(self, llm):
        """Every correction the graph injected, read off the last history the model was sent."""
        return [message.content for message in llm.calls[-1] if isinstance(message, HumanMessage)]


# -------------------------------------------------------------------------------------------
# Exercise 1 — a turn that changed nothing
# The guard you write. `assistant_thinking(...)` is the turn it is about: reasoning on
# its own channel, empty content, and no tool call — the characteristic failure of a thinking
# model, on demand.
# -------------------------------------------------------------------------------------------


class TestWhatCountsAsActing(unittest.TestCase):
    """Blank 1. Two lines of test for one line of code, because everything else rests on it."""

    def test_a_turn_with_a_tool_call_acted(self):
        self.assertTrue(
            acted(assistant_tool_call("run_tests", {})),
            "a turn that called a tool asked for something to happen",
        )

    def test_a_turn_that_only_reasoned_did_not_act(self):
        """Three hundred tokens of deliberation is not an action."""
        self.assertFalse(
            acted(assistant_thinking(THOUGHT)),
            "reasoning is not an action: this turn moved nothing",
        )

    def test_prose_is_not_an_action_either(self):
        self.assertFalse(
            acted(assistant_text("I have fixed the bug.")),
            "prose is not an action either",
        )

    def test_reasoning_does_not_stop_a_tool_call_from_counting(self):
        """ReAct means thinking and acting in the SAME turn. That turn acted."""
        self.assertTrue(
            acted(assistant_tool_call("run_tests", {}, reasoning=THOUGHT)),
            "the reasoning is not what decides this — the tool call is",
        )


class TestTheThinkingGuardEndsTheRun(GraphTestCase):
    def test_a_model_that_only_thinks_is_abandoned_rather_than_nudged_forever(self):
        # A generous step budget, so that what stops this run is the guard and not the budget.
        # Without the guard the script would be nudged around the loop until the budget ran out,
        # spending the most expensive kind of turn there is each time round.
        result, llm = self.run_with(
            [assistant_thinking(f"{THOUGHT} Let me consider it further. #{n}") for n in range(9)],
            max_steps=9,
        )
        self.assertFalse(result.solved)
        # The literal 2, deliberately, not MAX_IDLE_TURNS. Asserting against the constant the
        # code already uses is a tautology — it would pass at any value, so it pins nothing.
        self.assertEqual(result.steps_used, 2, "two wasted turns and the run stops")
        self.assertLess(llm.index, 9, "the run stopped early instead of burning the budget")

    def test_the_thinking_budget_is_two_turns(self):
        """The value itself, stated once, so a change to it cannot pass silently."""
        self.assertEqual(MAX_IDLE_TURNS, 2)

    def test_a_silent_turn_counts_the_same_as_a_thinking_one(self):
        """`idle_turns` is about what the turn MOVED, not about whether it deliberated.

        A reply cut off by `max_tokens` before it reached its tool call reasoned and asked for
        nothing; a model that skipped thinking and said "looks fine" did neither. Both changed
        nothing, and both cost the same.
        """
        result, _ = self.run_with(
            [assistant_text(f"Looks fine to me. #{n}") for n in range(9)], max_steps=9
        )
        self.assertFalse(result.solved)
        self.assertEqual(result.steps_used, 2)

    def test_the_trace_says_why_the_run_was_abandoned(self):
        """A run that ends on a guard and a run that ends on the budget are different failures."""
        tracer = Tracer()
        self.run_with(
            [assistant_thinking(f"thinking {n}") for n in range(4)], max_steps=4, tracer=tracer
        )
        notes = [event.detail for event in tracer.events]
        self.assertTrue(
            any("no tool call" in note for note in notes),
            f"the abandonment should be recorded, got {notes}",
        )


class TestThinkingIsStillAllowed(GraphTestCase):
    """The other half of the decision, and the easier half to get wrong."""

    def test_one_thinking_turn_is_allowed_because_planning_is_not_stalling(self):
        """MAX_IDLE_TURNS is 2, not 1 — a turn spent planning must not end the run."""
        result, llm = self.run_with(
            [
                assistant_tool_call("run_tests", {}),
                assistant_thinking("The failure is in total(). Planning the fix."),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("Fixed the off-by-one."),
            ],
            max_steps=6,
        )
        self.assertTrue(result.solved, "one thinking turn mid-run is legitimate")
        self.assertIn(NUDGE_AFTER_THINKING, self.nudges_sent(llm))

    def test_acting_resets_the_idle_count(self):
        """Blank 2, and the reason it cannot be a reducer.

        Two thinking turns that are not consecutive are not a stall. Without the reset they
        accumulate, and this run is abandoned on its fourth turn with a fix it never wrote.
        """
        result, _ = self.run_with(
            [
                assistant_thinking("Let me plan."),
                assistant_tool_call("run_tests", {}),
                assistant_thinking("Now let me think again."),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("Fixed it."),
            ],
            max_steps=8,
        )
        self.assertTrue(result.solved)
        self.assertEqual((self.tmp / "cart.py").read_text(), FIXED)


class TestTheTestsStillDecide(GraphTestCase):
    """Ordering. The guard must not be reached on a turn that had nothing left to do."""

    def test_a_run_still_ends_successfully_when_the_tests_pass(self):
        result, _ = self.run_with(
            [
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("Fixed the off-by-one."),
            ]
        )
        self.assertTrue(result.solved)

    def test_a_thinking_turn_on_a_green_suite_ends_the_run_rather_than_being_nudged(self):
        """The closing turn of a solved run is a turn that asked for nothing.

        Here it is a turn that reasoned about the fix and stopped, which is exactly what this
        model does when the work is done. `is_done` has to be read before anything else in the
        tail — a tail that nudges first asks for a fifth turn that was never scripted, and one
        that guards first would report a solved run as a stalled model.
        """
        result, llm = self.run_with(
            [
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_thinking("The suite is green, so the rounding fix was the whole bug."),
            ]
        )
        self.assertTrue(result.solved)
        self.assertEqual(llm.index, 3, "the graph took exactly the scripted turns")

    def test_a_model_that_merely_claims_success_is_not_believed(self):
        """The tests were never run, so nothing is green — whatever the model reasoned its way to."""
        result, _ = self.run_with(
            [
                assistant_text("I have fixed the bug.", reasoning="The subtraction was wrong."),
                assistant_text("Really, it is fixed."),
            ],
            max_steps=2,
        )
        self.assertFalse(result.solved, "prose is not evidence, and neither is reasoning")

    def test_the_step_budget_still_ends_the_run(self):
        """No nudging past the budget, or a stubborn model never stops.

        The tool call on every turn keeps `idle_turns` at zero, so the thinking guard never
        fires here and the budget is the only thing left to stop the run.
        """
        result, llm = self.run_with(
            [assistant_tool_call("list_files", {}) for _ in range(10)], max_steps=3
        )
        self.assertFalse(result.solved)
        self.assertEqual(llm.index, 3, "the run must not exceed its step budget")


class TestTheNudgeMatchesTheFailure(GraphTestCase):
    """Blank 4. Two nudges, because the two failures deserve different corrections."""

    def test_a_model_that_reasoned_and_did_not_act_is_told_reasoning_is_not_an_action(self):
        _, llm = self.run_with(
            [
                assistant_thinking(THOUGHT),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("Fixed it."),
            ],
            max_steps=6,
        )
        self.assertIn(NUDGE_AFTER_THINKING, self.nudges_sent(llm))

    def test_a_model_that_said_nothing_useful_gets_the_plain_nudge(self):
        """Nothing to correct about the reasoning of a turn that did not reason."""
        _, llm = self.run_with(
            [
                assistant_text("I think it is fine."),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("Fixed it."),
            ],
            max_steps=6,
        )
        self.assertIn(NUDGE, self.nudges_sent(llm))


# -------------------------------------------------------------------------------------------
# The agent: the graph, the budget, the stop condition and the loop guard
# Nothing is patched. The suite really is red before the agent's write and really is
# green after it, which is what makes a passing test here mean something.
# -------------------------------------------------------------------------------------------


class TestConstants(GraphTestCase):
    """The budgets themselves, stated as literals once so a change cannot pass silently."""

    def test_the_repeat_budget_is_three_strikes(self):
        self.assertEqual(MAX_GUARD_HITS, 3)


class TestSystemPrompt(GraphTestCase):
    def test_the_tool_names_come_from_the_registered_tools(self):
        """Derived, not hardcoded, so the prompt and the schemas cannot drift apart."""
        prompt = system_prompt(self.tools)
        for name in ("list_files", "read_file", "write_file", "run_tests"):
            self.assertIn(name, prompt)


class TestHappyPath(GraphTestCase):
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


class TestFailuresBecomeObservations(GraphTestCase):
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


class TestStopCondition(GraphTestCase):
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


class TestBudgetAndGuard(GraphTestCase):
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
        self.assertLessEqual(llm.index, 4)

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
            any("abandoned" in a and "3" in a for a in answers),
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


class TestToolNodeContract(GraphTestCase):
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


class TestCheckpointing(GraphTestCase):
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


class TestTracing(GraphTestCase):
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


# -------------------------------------------------------------------------------------------
# Reasoning: what makes this the ReAct edition
# Reasoning is read from the right place and counted, the trace reports it, the action
# guard ignores it, and a reply the client cannot read costs a turn rather than the run.
# -------------------------------------------------------------------------------------------


class TestReasoningIsReadFromTheRightPlace(unittest.TestCase):
    def test_reasoning_comes_off_additional_kwargs(self):
        message = assistant_tool_call("run_tests", {}, reasoning=THOUGHT)
        self.assertEqual(reasoning_of(message), THOUGHT)

    def test_a_turn_without_reasoning_reads_as_empty_not_as_none(self):
        """`reasoning_of` always returns a string, so every caller can treat it as falsy."""
        self.assertEqual(reasoning_of(assistant_tool_call("run_tests", {})), "")

    def test_reasoning_does_not_leak_into_the_answer(self):
        """`content` is the answer only. A thinking turn that acts usually has no answer at all.

        This is the property `reasoning=True` buys: with it unset the same text would arrive
        inline in `content` wrapped in `<think>` tags, and `write_file` would be handed a
        "complete file" with a monologue at the top of it.
        """
        message = assistant_tool_call("run_tests", {}, reasoning=THOUGHT)
        self.assertEqual(message.text, "")

    def test_a_thinking_turn_asked_for_nothing(self):
        self.assertFalse(acted(assistant_thinking(THOUGHT)))

    def test_a_turn_that_reasoned_and_called_a_tool_counts_as_acting(self):
        self.assertTrue(acted(assistant_tool_call("run_tests", {}, reasoning=THOUGHT)))


class TestTheTraceReportsReasoning(unittest.TestCase):
    """The regression the port could most easily have shipped, and shipped silently."""

    def test_describe_does_not_claim_no_reasoning_when_the_model_reasoned(self):
        message = assistant_tool_call("run_tests", {}, reasoning=THOUGHT)
        summary = describe(message)
        self.assertIn("calls run_tests", summary)
        self.assertNotIn("NO REASONING", summary)

    def test_describe_still_flags_a_turn_that_acted_without_thinking(self):
        """The marker survives, but now it means what it says."""
        self.assertIn("NO REASONING", describe(assistant_tool_call("run_tests", {})))

    def test_verbose_output_prints_the_thinking_under_the_turn(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            Tracer(verbose=True).record(
                TraceEvent(1, "llm", "assistant", "calls run_tests", 120, 0.4, THOUGHT)
            )
        printed = buffer.getvalue()
        self.assertIn("thinks", printed)
        self.assertIn("subtraction", printed)
        self.assertEqual(
            len(printed.strip().splitlines()), 2, "the action line, then the thinking under it"
        )

    def test_reasoning_survives_into_the_eval_json(self):
        tracer = Tracer()
        tracer.record(TraceEvent(1, "llm", "assistant", "calls run_tests", 10, 0.1, THOUGHT))
        self.assertEqual(tracer.as_json()[0]["reasoning"], THOUGHT)


class TestReasoningIsCounted(GraphTestCase):
    def test_only_the_turns_that_reasoned_are_counted(self):
        result, _ = self.run_with(
            [
                assistant_tool_call("run_tests", {}, reasoning=THOUGHT),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}, reasoning="Confirming the fix."),
                assistant_text("Fixed the off-by-one.", reasoning="It passes now."),
            ]
        )
        self.assertTrue(result.solved)
        self.assertEqual(result.steps_used, 4)
        self.assertEqual(result.reasoning_turns, 3)

    def test_an_agent_that_never_reasons_reports_zero(self):
        """The previous edition's measured result, reproducible here on demand."""
        result, _ = self.run_with(
            [
                assistant_tool_call("run_tests", {}),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("done"),
            ]
        )
        self.assertTrue(result.solved)
        self.assertEqual(result.reasoning_turns, 0)


class TestTheActionGuardIgnoresReasoning(GraphTestCase):
    """Fresh reasoning must not buy a repeated call another turn."""

    def test_the_signature_is_the_same_whatever_the_model_was_thinking(self):
        call = {"name": "read_file", "args": {"path": "cart.py"}, "id": "c1"}
        self.assertEqual(call_signature(call), call_signature(dict(call)))

    def test_the_same_call_with_different_reasoning_is_still_guarded(self):
        """The hole a reasoning-aware signature would have opened.

        A small model rarely repeats itself word for word — it talks itself into the same dead
        end by a slightly different route. Include the reasoning in the signature and every
        repeat looks novel, the guard never fires, and the run burns its budget re-reading one
        file.
        """
        tracer = Tracer()
        result, _ = self.run_with(
            [
                assistant_tool_call("read_file", {"path": "cart.py"}, reasoning="Let me look."),
                assistant_tool_call(
                    "read_file", {"path": "cart.py"}, reasoning="On reflection, look again."
                ),
                assistant_tool_call(
                    "read_file", {"path": "cart.py"}, reasoning="A third, quite different, look."
                ),
                assistant_text("done"),
            ],
            max_steps=4,
            tracer=tracer,
        )
        self.assertFalse(result.solved)
        guarded = [event for event in tracer.events if "guarded" in event.detail]
        self.assertTrue(guarded, "the repeat should have been refused despite new reasoning")


class TestAnUnreadableReplyIsRecoverable(GraphTestCase):
    """A reply the client cannot parse must cost a turn, not the run.

    `ChatOllama` raises `OutputParserException` on malformed tool-call arguments rather than
    reporting them as a call it could not read, so this is the only bad-JSON path there is. Before the recovery it propagated out of the agent and the task was
    recorded as a CRASH: a harness failure, for what is really just the model getting one reply
    wrong.
    """

    def test_the_run_survives_and_still_solves(self):
        result, llm = self.run_with(
            [
                assistant_tool_call("run_tests", {}, reasoning="Measure first."),
                unreadable_reply(),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("Fixed the off-by-one."),
            ],
            max_steps=8,
        )
        self.assertTrue(result.solved, "one unreadable reply must not end the run")
        self.assertEqual(llm.index, 5, "every scripted turn was used, including the bad one")

    def test_the_bad_turn_still_costs_a_step(self):
        """It consumed a model call, so it is on the bill. Otherwise the budget is a lie."""
        result, _ = self.run_with(
            [
                unreadable_reply(),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("done"),
            ],
            max_steps=6,
        )
        self.assertTrue(result.solved)
        self.assertEqual(result.steps_used, 4)

    def test_the_model_is_told_what_went_wrong(self):
        """A retry with no explanation is just the same reply again."""
        from langchain_core.messages import HumanMessage

        _, llm = self.run_with(
            [
                unreadable_reply(),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("done"),
            ],
            max_steps=6,
        )
        sent = [m.content for m in llm.calls[-1] if isinstance(m, HumanMessage)]
        self.assertIn(UNREADABLE_REPLY, sent)

    def test_no_assistant_turn_is_fabricated_for_a_reply_that_never_parsed(self):
        """There was no message. Inventing an empty one would put words in the model's mouth."""
        from langchain_core.messages import AIMessage

        _, llm = self.run_with(
            [
                unreadable_reply(),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("done"),
            ],
            max_steps=6,
        )
        # The history the model saw on its second turn: one system, one task, one correction.
        second_turn = llm.calls[1]
        self.assertEqual(
            [m for m in second_turn if isinstance(m, AIMessage)],
            [],
            "the unreadable turn must leave no assistant message behind",
        )

    def test_a_model_that_never_emits_readable_json_is_abandoned(self):
        """Bounded by the same guard as thinking: two turns that changed nothing, and stop."""
        result, llm = self.run_with([unreadable_reply() for _ in range(9)], max_steps=9)
        self.assertFalse(result.solved)
        self.assertEqual(result.steps_used, 2)
        self.assertLess(llm.index, 9, "it gave up instead of burning the whole budget")

    def test_an_unreadable_reply_on_the_last_allowed_turn_ends_the_run(self):
        """The step budget still applies to a turn that could not be read.

        Otherwise the retry would be free: a reply that failed to parse on the final permitted
        turn would be sent back to the model, buying a turn the budget had already spent.
        """
        result, llm = self.run_with([unreadable_reply()], max_steps=1)
        self.assertFalse(result.solved)
        self.assertEqual(result.steps_used, 1)
        self.assertEqual(llm.index, 1, "no extra turn was granted by the retry path")

    def test_a_readable_turn_resets_the_allowance(self):
        """Otherwise two bad replies far apart in a long run would end it."""
        result, _ = self.run_with(
            [
                unreadable_reply(),
                assistant_tool_call("run_tests", {}),
                unreadable_reply(),
                assistant_tool_call("write_file", {"path": "cart.py", "content": FIXED}),
                assistant_tool_call("run_tests", {}),
                assistant_text("done"),
            ],
            max_steps=8,
        )
        self.assertTrue(result.solved)

    def test_the_trace_records_it(self):
        tracer = Tracer()
        self.run_with([unreadable_reply(), unreadable_reply()], max_steps=4, tracer=tracer)
        details = [event.detail for event in tracer.events]
        self.assertTrue(
            any("unreadable reply" in d for d in details),
            f"the failure should be visible in the trace, got {details}",
        )

    def test_a_connection_error_is_not_swallowed(self):
        """Only a parse failure is recoverable. A dead server must still stop the run.

        The recovery catches `OutputParserException` specifically and not `Exception`, because
        "the model said something odd" and "there is no model" need opposite responses.
        """
        llm = FakeChatModel(replies=[ConnectionError("server went away")])
        with self.assertRaises(ConnectionError):
            run_agent(self.task, llm, self.tools, max_steps=4)


# -------------------------------------------------------------------------------------------
# The state: how one node's update combines with what is already there
# -------------------------------------------------------------------------------------------


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


# -------------------------------------------------------------------------------------------
# The trace: what makes a run debuggable rather than just pass/fail
# -------------------------------------------------------------------------------------------


class TestTracer(unittest.TestCase):
    def test_events_are_collected_in_order(self):
        tracer = Tracer()
        tracer.record(TraceEvent(1, "llm", "assistant", "calls run_tests", 120, 0.4))
        tracer.record(TraceEvent(1, "tool", "run_tests", "Tests failed.", 120, 0.3))
        self.assertEqual([e.kind for e in tracer.events], ["llm", "tool"])

    def test_quiet_by_default(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            Tracer().record(TraceEvent(1, "llm", "assistant", "x", 1, 0.1))
        self.assertEqual(buffer.getvalue(), "")

    def test_verbose_prints_one_line_per_event(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            Tracer(verbose=True).record(
                TraceEvent(2, "tool", "run_tests", "Tests failed.", 300, 1.2)
            )
        printed = buffer.getvalue()
        self.assertIn("step 2", printed)
        self.assertIn("run_tests", printed)
        self.assertIn("300 tok", printed)
        self.assertEqual(len(printed.strip().splitlines()), 1)

    def test_newlines_are_flattened_and_long_detail_is_clipped(self):
        """One event, one line — a readable trace beats a faithful dump of file contents."""
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            Tracer(verbose=True).record(
                TraceEvent(1, "tool", "read_file", "a\nb\n" + "x" * 5000, 10, 0.0)
            )
        printed = buffer.getvalue()
        self.assertEqual(len(printed.strip().splitlines()), 1)
        self.assertLess(len(printed), DETAIL_CLIP + 200)

    def test_as_json_is_serialisable_for_the_eval_report(self):
        tracer = Tracer()
        tracer.record(TraceEvent(1, "llm", "assistant", "x", 120, 0.4))
        self.assertEqual(tracer.as_json()[0]["prompt_tokens"], 120)
        self.assertEqual(tracer.as_json()[0]["step"], 1)


class TestCallbackHooks(unittest.TestCase):
    """The hooks LangChain actually calls, driven directly — no graph, no model.

    These were previously only executed, never asserted on: a Tracer whose `step` never advanced,
    or whose `on_tool_error` did nothing, passed the entire suite.
    """

    def _llm_result(self, message):
        return LLMResult(generations=[[ChatGeneration(message=message)]])

    def test_each_model_turn_advances_the_step_and_records_its_context_size(self):
        tracer = Tracer()
        for turn, tokens in enumerate((99, 250), start=1):
            run_id = uuid4()
            tracer.on_chat_model_start({}, [[]], run_id=run_id)
            tracer.on_llm_end(
                self._llm_result(assistant_text("hi", prompt_tokens=tokens)), run_id=run_id
            )
            self.assertEqual(tracer.events[-1].step, turn)
            self.assertEqual(tracer.events[-1].prompt_tokens, tokens)

    def test_a_tool_event_carries_the_step_and_context_of_the_turn_that_asked_for_it(self):
        tracer = Tracer()
        llm_run = uuid4()
        tracer.on_chat_model_start({}, [[]], run_id=llm_run)
        tracer.on_llm_end(
            self._llm_result(assistant_tool_call("run_tests", {}, prompt_tokens=1234)),
            run_id=llm_run,
        )
        tool_run = uuid4()
        tracer.on_tool_start({"name": "run_tests"}, "{}", run_id=tool_run)
        tracer.on_tool_end(
            ToolMessage(content="All tests passed.", name="run_tests", tool_call_id="c1"),
            run_id=tool_run,
        )
        event = tracer.events[-1]
        self.assertEqual((event.kind, event.name), ("tool", "run_tests"))
        self.assertEqual(event.step, 1)
        self.assertEqual(event.prompt_tokens, 1234)

    def test_a_tool_that_raises_is_recorded_by_name(self):
        """`on_tool_error` is not told the name; it has to be remembered from on_tool_start."""
        tracer = Tracer()
        run_id = uuid4()
        tracer.on_tool_start({"name": "read_file"}, "{}", run_id=run_id)
        tracer.on_tool_error(RuntimeError("disk on fire"), run_id=run_id)
        event = tracer.events[-1]
        self.assertEqual(event.name, "read_file", "an unnamed error event is the useless one")
        self.assertIn("RuntimeError", event.detail)
        self.assertIn("disk on fire", event.detail)

    def test_a_reply_with_no_assistant_message_is_noted_rather_than_dropped(self):
        """A raise in a hook is swallowed by LangChain, so this must not raise — and a silent
        return would leave the turn invisible and the next tool line stamped with stale tokens.
        """
        tracer = Tracer()
        run_id = uuid4()
        tracer.on_chat_model_start({}, [[]], run_id=run_id)
        tracer.on_llm_end(
            self._llm_result(assistant_text("real", prompt_tokens=500)), run_id=run_id
        )

        second = uuid4()
        tracer.on_chat_model_start({}, [[]], run_id=second)
        tracer.on_llm_end(
            LLMResult(generations=[[Generation(text="not a chat reply")]]), run_id=second
        )
        self.assertEqual(tracer.events[-1].step, 2)
        self.assertIn("no assistant message", tracer.events[-1].detail)
        self.assertEqual(tracer.turn_prompt_tokens, 0, "a turn with no reply must not inherit 500")

    def test_an_empty_generation_list_does_not_raise(self):
        """`response.generations[0][0]` used to IndexError here, and LangChain would eat it."""
        tracer = Tracer()
        run_id = uuid4()
        tracer.on_chat_model_start({}, [[]], run_id=run_id)
        tracer.on_llm_end(LLMResult(generations=[]), run_id=run_id)
        self.assertIn("no assistant message", tracer.events[-1].detail)


# -------------------------------------------------------------------------------------------
# The model layer: the scripted fake, and the real client's configuration
# Constructed, never called — there is no network in any of this.
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

    def test_this_edition_has_its_own_model_variable(self):
        """The failure this prevents is a silent one.

        `setup.py --tier mellum2` records two models: a coding one in MELLUM_MODEL and a
        thinking one in AGENTGRAPH_MODEL. If this edition read MELLUM_MODEL first it would run
        on the coding model, which has no thinking mode — a working agent that has quietly
        become the Act-only one from the previous lesson. So the two must not be interchangeable,
        and AGENTGRAPH_MODEL has to win when both are set.
        """
        import os
        from unittest import mock

        with mock.patch.dict(
            os.environ,
            {
                "MELLUM_MODEL": "agentfix-mellum2",
                "AGENTGRAPH_MODEL": "agentgraph-mellum2-thinking",
            },
        ):
            self.assertEqual(LLMConfig.from_env().model, "agentgraph-mellum2-thinking")

        # ... and MELLUM_MODEL still works on its own, for a hand-built model.
        with mock.patch.dict(os.environ, {"MELLUM_MODEL": "my-own-thinking-build"}, clear=True):
            self.assertEqual(LLMConfig.from_env().model, "my-own-thinking-build")

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(LLMConfig.from_env().model, DEFAULT_MODEL)

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

    def test_reasoning_is_requested(self):
        """The one flag that makes this the ReAct edition.

        Without it the Thinking model still thinks, but the `<think>` tags arrive INLINE in the
        answer — so the trace prints them as prose and `write_file` is handed a "complete file"
        with a monologue at the top. `/v1` has no concept of this parameter at all, which is
        why the edition could not have been built on ChatOpenAI.
        """
        self.assertTrue(make_chat_model(LLMConfig()).reasoning)

    def test_reasoning_can_be_turned_off_to_reproduce_the_previous_edition(self):
        self.assertFalse(make_chat_model(LLMConfig(reasoning=False)).reasoning)

    def test_top_k_reaches_the_server(self):
        """JetBrains publishes top_k=20 for the Thinking checkpoint; the Instruct edition had none."""
        self.assertEqual(make_chat_model(LLMConfig(top_k=20)).top_k, 20)

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
# The sandbox: where the agent's test runs actually execute
# The Docker argv is asserted without a Docker daemon present.
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

    def test_a_hanging_test_times_out_as_a_result_rather_than_an_exception(self):
        self._write_test(
            "import unittest, time\n\n\nclass T(unittest.TestCase):\n"
            "    def test_hang(self):\n        time.sleep(30)\n"
        )
        result = SubprocessBackend().run(self.tmp, UNITTEST_CMD, timeout_s=2)
        self.assertTrue(result.timed_out)
        self.assertFalse(result.passed)
        self.assertIn("TIMEOUT", result.output)

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
            "        self.assertIsNone(os.environ.get('AGENTGRAPH_SECRET'))\n"
        )
        with mock.patch.dict(os.environ, {"AGENTGRAPH_SECRET": "leaked"}):
            self.assertTrue(SubprocessBackend().run(self.tmp, UNITTEST_CMD, timeout_s=30).passed)


class TestBackendSelection(unittest.TestCase):
    def test_defaults_to_subprocess(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsInstance(get_backend(), SubprocessBackend)

    def test_the_environment_variable_selects_docker(self):
        with mock.patch.dict(os.environ, {"AGENTGRAPH_SANDBOX": "docker"}):
            self.assertIsInstance(get_backend(), DockerBackend)

    def test_an_explicit_argument_wins_over_the_environment(self):
        with mock.patch.dict(os.environ, {"AGENTGRAPH_SANDBOX": "docker"}):
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
# The tools: the agent's only way to touch the world
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


# -------------------------------------------------------------------------------------------
# Tasks and wiring: the disposable workspace, and the pieces assembled
# -------------------------------------------------------------------------------------------


class TestRepoRoot(unittest.TestCase):
    """Where the CLI looks for fixtures and writes results, and why it is not counted levels.

    This package ships in two shapes — `src/agentgraph/` in the repository, `agentgraph/` at the
    root of a JetBrains Academy task directory — and a fixed `.parents[n]` cannot be right for
    both. Counted from the package in the Academy layout it landed on the lesson directory,
    where there are no fixtures at all: `eval` then found no tasks and wrote its results
    somewhere nobody would look.
    """

    def test_the_root_is_the_directory_that_holds_the_fixtures(self):
        self.assertTrue((REPO_ROOT / "tasks" / "workshop").is_dir())

    def test_the_packages_own_tasks_module_is_not_mistaken_for_the_fixtures(self):
        """`agentgraph/tasks/` is a subpackage, and searching from the package would match it."""
        package_dir = Path(agentgraph.config.__file__).resolve().parent
        self.assertTrue((package_dir / "tasks").is_dir(), "the trap this test is about")
        self.assertNotEqual(REPO_ROOT, package_dir)


class TestLoadTask(TempDirTestCase):
    def _task_dir(self, meta: dict) -> None:
        (self.tmp / "repo").mkdir(exist_ok=True)
        (self.tmp / "task.json").write_text(json.dumps(meta), encoding="utf-8")

    def test_reads_every_field(self):
        self._task_dir(
            {
                "task_id": "demo",
                "test_command": ["-m", "unittest", "discover", "-q"],
                "expected_failures": ["test_x"],
                "prompt": "Fix the bug.",
            }
        )
        task = load_task(self.tmp)
        self.assertEqual(task.task_id, "demo")
        self.assertEqual(task.expected_failures, ("test_x",))
        self.assertEqual(task.prompt, "Fix the bug.")
        self.assertEqual(task.template_dir, self.tmp / "repo")

    def test_a_minimal_task_json_still_loads(self):
        self._task_dir({})
        task = load_task(self.tmp)
        self.assertEqual(task.task_id, self.tmp.name)
        self.assertEqual(task.prompt, DEFAULT_PROMPT)
        self.assertEqual(task.test_command[1:], ("-m", "unittest", "discover", "-q"))

    def test_a_flag_first_command_is_pinned_to_this_interpreter(self):
        """ "python" on the sandbox PATH is not necessarily the project's virtualenv."""
        self._task_dir({"test_command": ["-m", "unittest"]})
        self.assertEqual(load_task(self.tmp).test_command[0], sys.executable)

    def test_a_command_naming_its_own_program_is_left_alone(self):
        self._task_dir({"test_command": ["/usr/bin/make", "test"]})
        self.assertEqual(load_task(self.tmp).test_command, ("/usr/bin/make", "test"))


class TestWorkspace(TempDirTestCase):
    def _task(self):
        from agentgraph.tasks.loader import Task

        template = self.tmp / "repo"
        template.mkdir()
        (template / "a.py").write_text("original\n", encoding="utf-8")
        return Task("t", self.tmp, template, ("true",), (), "p")

    def test_the_copy_is_writable_and_the_template_is_untouched(self):
        task = self._task()
        with workspace(task) as work_dir:
            (work_dir / "a.py").write_text("rewritten\n", encoding="utf-8")
        self.assertEqual((task.template_dir / "a.py").read_text(), "original\n")

    def test_a_read_only_template_still_produces_a_writable_copy(self):
        """Workshop fixtures are often handed out read-only; the copy must not inherit that."""
        task = self._task()
        (task.template_dir / "pkg").mkdir()
        (task.template_dir / "pkg" / "b.py").write_text("original\n", encoding="utf-8")
        for path in (
            task.template_dir / "pkg" / "b.py",
            task.template_dir / "a.py",
            task.template_dir / "pkg",
            task.template_dir,
        ):
            path.chmod(0o555 if path.is_dir() else 0o444)
        try:
            with workspace(task) as work_dir:
                (work_dir / "a.py").write_text("rewritten\n", encoding="utf-8")
                # A rename into the directory, which is what write_file actually does.
                scratch = work_dir / "pkg" / "b.py.tmp"
                scratch.write_text("rewritten\n", encoding="utf-8")
                scratch.replace(work_dir / "pkg" / "b.py")
                self.assertEqual((work_dir / "pkg" / "b.py").read_text(), "rewritten\n")
                captured = work_dir
        finally:
            for path in (task.template_dir, task.template_dir / "pkg"):
                path.chmod(0o755)

        # The read-only template is unchanged, and the copy was still cleaned up.
        self.assertEqual((task.template_dir / "a.py").read_text(), "original\n")
        self.assertEqual(stat.S_IMODE((task.template_dir / "a.py").stat().st_mode), 0o444)
        self.assertFalse(captured.exists())

    def test_the_copy_is_deleted_on_the_way_out(self):
        with workspace(self._task()) as work_dir:
            captured = work_dir
        self.assertFalse(captured.exists())

    def test_the_copy_is_deleted_even_when_the_body_raises(self):
        """Without the try/finally, every failed run would leak a copy of the project."""
        captured = None
        with self.assertRaises(RuntimeError):
            with workspace(self._task()) as work_dir:
                captured = work_dir
                raise RuntimeError("tool crashed")
        self.assertIsNotNone(captured)
        self.assertFalse(captured.exists())


class TestSolveTask(TempDirTestCase):
    def test_the_real_wiring_solves_a_real_fixture_with_a_scripted_model(self):
        llm = FakeChatModel(
            replies=[
                assistant_tool_call("run_tests", {}),
                assistant_tool_call("read_file", {"path": "shopcart/cart.py"}),
                assistant_tool_call(
                    "write_file", {"path": "shopcart/cart.py", "content": SHOPCART_FIXED}
                ),
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
                assistant_tool_call(
                    "write_file", {"path": "shopcart/cart.py", "content": SHOPCART_FIXED}
                ),
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
                assistant_tool_call(
                    "write_file", {"path": "shopcart/cart.py", "content": SHOPCART_FIXED}
                ),
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


# -------------------------------------------------------------------------------------------
# The oracle: the tests cannot be made to pass without fixing the bug
# Every case here was a reproduced escape before the check that stops it existed, so
# these are regression tests rather than hypotheticals.
# -------------------------------------------------------------------------------------------


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


# -------------------------------------------------------------------------------------------
# The shipped fixtures: every task starts red, for the reason it claims
# The cheapest way for a workshop to waste twenty minutes is a task that is already
# green, or red for the wrong reason. These run the real suites, so they catch it.
# -------------------------------------------------------------------------------------------


class TestWorkshopFixtures(unittest.TestCase):
    def test_there_are_fixtures_to_run(self):
        self.assertGreaterEqual(len(TASK_DIRS), 3)

    def test_each_fixture_starts_red_with_the_failures_it_declares(self):
        for task_dir in TASK_DIRS:
            with self.subTest(task=task_dir.name):
                task = load_task(task_dir)
                with workspace(task) as work_dir:
                    completed = subprocess.run(
                        list(task.test_command),
                        cwd=work_dir,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                combined = completed.stdout + completed.stderr
                self.assertNotEqual(completed.returncode, 0, "fixture is already green")
                self.assertTrue(task.expected_failures, "a fixture must declare its failures")
                for name in task.expected_failures:
                    self.assertIn(name, combined)

    def test_each_fixture_has_a_discoverable_test_package(self):
        """Without tests/__init__.py, `unittest discover` finds nothing and exits 5."""
        for task_dir in TASK_DIRS:
            with self.subTest(task=task_dir.name):
                tests_dir = task_dir / "repo" / "tests"
                if tests_dir.is_dir():
                    self.assertTrue((tests_dir / "__init__.py").exists())

    def test_discovery_actually_finds_more_than_zero_tests(self):
        for task_dir in TASK_DIRS:
            with self.subTest(task=task_dir.name):
                task = load_task(task_dir)
                with workspace(task) as work_dir:
                    completed = subprocess.run(
                        [sys.executable, "-m", "unittest", "discover"],
                        cwd=work_dir,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                self.assertNotIn("NO TESTS RAN", completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
