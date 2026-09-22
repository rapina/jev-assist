"""Unit tests for the decisions jev_server makes on every call.

The server itself is a long-lived process on loopback, so these tests hold the
parts that do not need it: which model serves a Codex-dry call, which rung of
that model's ladder the decided depth lands on, whether a failed tandem call is
worth one attempt on the sibling model, and the confidence gate that keeps the
triptych from over-spending.
"""
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import jev_server as jev


class ResponseIdContinuity(unittest.TestCase):
    """One response id per relayed stream, however many gateways touched it.

    A Codex-dry turn is relayed through the local edge, which encodes response
    ids, so the terminal event of the stream we receive repeats the id under a
    fresh encoding. The Responses transform in front of the router read that as a
    completion that renamed itself and replaced the turn with an
    `invalid_responses_stream` error -- the shape that ended a live tandem turn
    on 18 September 2026.
    """

    CREATED = b'data: {"type":"response.created","response":{"id":"resp_created"}}\n\n'
    DONE = b"data: [DONE]\n\n"

    def relay(self, *frames):
        markerer = jev.SummaryMarker(" \u00b7 \U0001f9e0sol:low \u00b7 ")
        return "".join(markerer.feed(frame) for frame in frames) + markerer.flush()

    def response_ids(self, stream):
        ids = []
        for line in stream.splitlines():
            if not line.startswith("data: ") or line[6:].strip() == "[DONE]":
                continue
            event = json.loads(line[6:])
            response = event.get("response")
            if isinstance(response, dict) and "id" in response:
                ids.append(response["id"])
        return ids

    def test_a_re_encoded_completion_keeps_the_announced_id(self):
        completed = (
            b'data: {"type":"response.completed","response":{"id":"resp_re-encoded","output":[]}}\n\n'
        )
        stream = self.relay(self.CREATED, completed, self.DONE)
        self.assertEqual(self.response_ids(stream), ["resp_created", "resp_created"])
        self.assertIn("data: [DONE]", stream)

    def test_every_terminal_event_is_rewritten_onto_the_announced_id(self):
        for terminal in ("response.completed", "response.incomplete", "response.failed"):
            frame = (
                'data: {"type":"%s","response":{"id":"resp_other","output":[]}}\n\n' % terminal
            ).encode()
            stream = self.relay(self.CREATED, frame)
            self.assertEqual(self.response_ids(stream), ["resp_created", "resp_created"], terminal)

    def test_a_stream_without_a_created_event_is_left_to_its_own_id(self):
        completed = (
            b'data: {"type":"response.completed","response":{"id":"resp_alone","output":[]}}\n\n'
        )
        stream = self.relay(completed)
        self.assertEqual(self.response_ids(stream), ["resp_alone"])


class QuotaReset(unittest.TestCase):
    """A flip lasts as long as the window stays shut, and not longer.

    The edge announces the reopening instant on an exhausted quota; the auto
    state follows it, so the first call after the reset is served by the native
    triptych again instead of waiting out a flat cooldown on a window that
    already came back.
    """

    def test_the_relative_header_wins_over_a_skewed_clock(self):
        now = time.time()
        at = jev.quota_reset_at(
            {
                "x-codex-primary-reset-after-seconds": "600",
                "x-codex-primary-reset-at": str(int(now) - 5),
            },
            b"",
        )
        self.assertAlmostEqual(at, now + 600, delta=5)

    def test_the_absolute_header_is_used_when_it_stands_alone(self):
        now = time.time()
        at = jev.quota_reset_at({"x-codex-primary-reset-at": str(int(now) + 900)}, b"")
        self.assertAlmostEqual(at, now + 900, delta=5)

    def test_the_body_announcement_is_a_fallback(self):
        now = time.time()
        body = json.dumps(
            {"error": {"type": "usage_limit_reached", "resets_in_seconds": 120}}
        ).encode()
        self.assertAlmostEqual(jev.quota_reset_at({}, body), now + 120, delta=5)

    def test_an_unusable_announcement_is_no_announcement(self):
        now = time.time()
        for headers, body in (
            ({}, b""),
            ({"x-codex-primary-reset-at": "soon"}, b""),
            ({"x-codex-primary-reset-after-seconds": "0"}, b""),
            ({"x-codex-primary-reset-at": str(int(now) - 60)}, b""),
            ({}, b"not json"),
            ({}, json.dumps({"error": {"message": "rate limit"}}).encode()),
        ):
            self.assertIsNone(jev.quota_reset_at(headers, body), (headers, body))







class Policy(unittest.TestCase):
    def test_a_low_confidence_user_turn_keeps_the_jev_choice(self):
        model, effort, speed, gate = jev.route(jev.LUNA, "low", 0.1, {"step_type": "user_turn"})
        self.assertEqual((model, effort, speed, gate), (jev.LUNA, "low", "default", "apply"))

    def test_a_clean_mechanical_step_keeps_luna_and_its_decided_depth(self):
        model, effort, speed, gate = jev.route(jev.LUNA, "low", 0.1, {"step_type": "tool_step"})
        self.assertEqual((model, effort, speed, gate), (jev.LUNA, "low", "default", "apply"))

    def test_a_confident_verdict_is_applied_as_given(self):
        model, effort, speed, gate = jev.route(jev.ASTRA, "max", 0.9, {"step_type": "user_turn"})
        self.assertEqual((model, effort, speed, gate), (jev.ASTRA, "max", "default", "apply"))

    def test_an_invalid_depth_does_not_silently_change_the_jev_choice(self):
        with self.assertRaises(ValueError):
            jev.route(jev.SOL, "nonsense", 0.9, {"step_type": "user_turn"})


class JevTaskInput(unittest.TestCase):
    """Jev judges the current ask: not the thread, and not Codex's own blocks.

    A turn carries machine-generated envelopes (goal context, plugin catalog,
    environment) that are far longer than the 500 chars Jev was calibrated on,
    and Codex appends some of them *after* the user's text. Clipping the raw head
    sent Jev nothing but the envelope on 1 291 of 4 516 live calls, so the task
    is unwrapped and clipped head+tail instead.
    """

    ASK = "Corrige le parseur de drift.test.ts, puis relance le backtest complet."
    GOAL = ('<codex_internal_context source="goal">\n'
            "Continue working toward the active thread goal.\n\n"
            "The objective below is a short navigation aid. "
            + ("navigation aid. " * 300) + "\n</codex_internal_context>")
    ENV = "<environment_context>\n<cwd>/Users/x/project</cwd>\n</environment_context>"
    PLUGINS = ("<recommended_plugins>\nHere is a list of plugins that are available "
               "but not installed.\n" + ("- Some Plugin (some-plugin@openai-curated-remote)\n" * 40)
               + "</recommended_plugins>")

    def user_item(self, text):
        return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}

    def task(self, text):
        return jev.extract({"input": [self.user_item(text)]})[0]

    def state(self, thread):
        payload = {"input": thread}
        task, prev_assistant, signals = jev.extract(payload)
        return jev.jev_state(task, prev_assistant, signals, jev.classify(payload))

    def test_a_goal_block_does_not_hide_the_ask(self):
        task = self.task(self.GOAL + "\n\n" + self.ASK)
        self.assertIn("Corrige le parseur", task)
        self.assertNotIn("codex_internal_context", task)

    def test_an_appended_environment_block_is_dropped(self):
        task = self.task(self.ASK + "\n" + self.ENV)
        self.assertIn("relance le backtest", task)
        self.assertNotIn("environment_context", task)

    def test_a_goal_only_turn_keeps_the_objective_and_loses_the_tags(self):
        task = self.task(self.GOAL)
        self.assertIn("Continue working toward the active thread goal.", task)
        self.assertNotIn("codex_internal_context", task)

    def test_an_envelope_without_a_request_gives_jev_nothing(self):
        """A catalog-only turn has no ask in it: the caller fails open."""
        self.assertEqual(self.task(self.PLUGINS), "")
        self.assertEqual(self.task(self.ENV), "")

    def test_the_tail_of_a_long_prompt_survives_the_clip(self):
        task = self.task("Contexte. " * 900 + self.ASK)
        self.assertIn("relance le backtest complet.", task)
        self.assertLessEqual(len(task), jev.TASK_CHARS)
        self.assertIn(jev.TASK_CLIP_MARK.strip(), task)

    def test_a_short_prompt_is_sent_untouched(self):
        self.assertEqual(self.task(self.ASK), self.ASK)

    def test_the_thread_length_never_reaches_jev(self):
        history = [self.user_item("Question %d" % i) for i in range(300)]
        assistant = {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "Reponse"}]}
        short = self.state([assistant, self.user_item(self.ASK)])
        long_thread = self.state(history + [assistant, self.user_item(self.ASK)])
        self.assertNotIn("n_items", long_thread)
        self.assertEqual(short["task"], long_thread["task"])
        self.assertLessEqual(len(json.dumps(long_thread)), 1100)

    def test_a_user_turn_contains_no_empty_or_historical_fields(self):
        assistant = {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "Old answer"}]}
        self.assertEqual(
            self.state([assistant, self.user_item(self.ASK)]),
            {"task": self.ASK, "step": "user_turn", "previous_proposal": "Old answer"},
        )

    def test_a_context_dependent_ask_gets_one_bounded_preceding_task(self):
        state = self.state([
            self.user_item("Analyse le routeur, corrige son cache puis valide les tests."),
            self.user_item(self.PLUGINS),
            self.user_item("Alors go ?"),
        ])
        self.assertEqual(state["task"], "Alors go ?")
        self.assertEqual(
            state["active_task"],
            "Analyse le routeur, corrige son cache puis valide les tests.",
        )
        self.assertLessEqual(len(state["active_task"]), jev.CONTEXT_TASK_CHARS)

    def test_short_asks_pay_only_for_bounded_context_without_guessing_intent(self):
        state = self.state([
            self.user_item("Refactor the entire authentication service."),
            self.user_item("List three files."),
        ])
        self.assertLessEqual(len(state["active_task"]), jev.CONTEXT_TASK_CHARS)

    def test_a_goal_summary_wins_for_continue_without_sending_the_envelope(self):
        goal = (
            '<codex_internal_context source="goal">'
            "Implement and verify the cache-safe model router."
            "</codex_internal_context>"
        )
        state = self.state([
            self.user_item("An older unrelated request."),
            self.user_item(goal + "\ncontinue"),
        ])
        self.assertEqual(state["task"], "continue")
        self.assertEqual(
            state["active_task"],
            "Implement and verify the cache-safe model router.",
        )
        self.assertNotIn("codex_internal_context", json.dumps(state))

    def test_only_the_last_tool_output_travels_and_only_as_a_digest(self):
        output = {"type": "function_call_output", "output": "ok\n" + ("ligne\n" * 5_000)}
        state = self.state([self.user_item(self.ASK), output])
        tail = state["tool_result_tail"]
        self.assertEqual(state["step"], "tool_step")
        self.assertEqual(len(tail), jev.DIGEST_CHARS)
        self.assertNotIn("contains_error", state)

    def test_the_tool_name_is_linked_by_call_id_without_sending_arguments(self):
        state = self.state([
            self.user_item(self.ASK),
            {"type": "function_call", "call_id": "call_1",
             "name": "exec_command", "arguments": "private arguments"},
            {"type": "function_call_output", "call_id": "call_1", "output": "done"},
        ])
        self.assertEqual(state["tool"], "exec_command")
        self.assertNotIn("private arguments", json.dumps(state))

    def test_the_assistant_side_is_bounded(self):
        assistant = {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "a" * 4_000}]}
        output = {"type": "function_call_output", "output": "done"}
        state = self.state([self.user_item(self.ASK), assistant, output])
        self.assertEqual(len(state["intent_tail"]), jev.INTENT_CHARS)


if __name__ == "__main__":
    unittest.main()
