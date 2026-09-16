#!/usr/bin/env python3
"""Tests for the c2c wire format (no browser, no filesystem)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chatgpt_protocol as proto


class ExtractOpsTest(unittest.TestCase):
    def test_reply_without_a_block_means_the_answer_is_final(self):
        self.assertIsNone(proto.extract_ops("The review is done. Nothing else."))

    def test_reads_ops_out_of_a_c2c_block(self):
        reply = 'Looking.\n\n```c2c\n{"ops":[{"op":"read","path":"a.py"}]}\n```\n'
        self.assertEqual(proto.extract_ops(reply), [{"op": "read", "path": "a.py"}])

    def test_fence_tag_is_case_insensitive(self):
        reply = '```C2C\n{"ops":[{"op":"list","path":"."}]}\n```'
        self.assertEqual(proto.extract_ops(reply), [{"op": "list", "path": "."}])

    def test_fence_tag_may_carry_trailing_spaces(self):
        reply = '```c2c   \n{"ops":[{"op":"list","path":"."}]}\n```'
        self.assertEqual(proto.extract_ops(reply), [{"op": "list", "path": "."}])

    def test_first_block_wins_when_the_model_emits_two(self):
        reply = (
            '```c2c\n{"ops":[{"op":"read","path":"first.py"}]}\n```\n'
            '```c2c\n{"ops":[{"op":"read","path":"second.py"}]}\n```'
        )
        self.assertEqual(proto.extract_ops(reply), [{"op": "read", "path": "first.py"}])

    def test_a_python_block_is_not_mistaken_for_a_request(self):
        reply = 'Suggested fix:\n\n```python\nprint("ops")\n```\n'
        self.assertIsNone(proto.extract_ops(reply))

    def test_broken_json_is_reported_not_swallowed(self):
        with self.assertRaises(proto.ProtocolError):
            proto.extract_ops('```c2c\n{"ops":[{"op":"read",}]}\n```')

    def test_a_bare_list_is_rejected(self):
        with self.assertRaises(proto.ProtocolError):
            proto.extract_ops('```c2c\n[{"op":"read"}]\n```')

    def test_object_without_ops_is_rejected(self):
        with self.assertRaises(proto.ProtocolError):
            proto.extract_ops('```c2c\n{"read":"a.py"}\n```')

    def test_empty_ops_is_rejected_so_the_loop_cannot_spin(self):
        with self.assertRaises(proto.ProtocolError):
            proto.extract_ops('```c2c\n{"ops":[]}\n```')

    def test_op_entries_must_be_objects(self):
        with self.assertRaises(proto.ProtocolError):
            proto.extract_ops('```c2c\n{"ops":["read a.py"]}\n```')

    def test_op_entries_must_name_an_op(self):
        with self.assertRaises(proto.ProtocolError):
            proto.extract_ops('```c2c\n{"ops":[{"path":"a.py"}]}\n```')

    def test_error_message_quotes_the_json_problem(self):
        try:
            proto.extract_ops('```c2c\nnot json at all\n```')
        except proto.ProtocolError as exc:
            self.assertIn("JSON", str(exc))
        else:
            self.fail("expected ProtocolError")


class FenceForTest(unittest.TestCase):
    """A body containing backticks must not be able to close its own fence."""

    def test_plain_body_uses_three_backticks(self):
        self.assertEqual(proto.fence_for("hello"), "```")

    def test_body_with_a_triple_fence_gets_four(self):
        self.assertEqual(proto.fence_for("a\n```\nb"), "````")

    def test_body_with_a_long_run_gets_one_more(self):
        self.assertEqual(proto.fence_for("`````"), "``````")

    def test_inline_backticks_do_not_widen_the_fence(self):
        self.assertEqual(proto.fence_for("use `x` here"), "```")


class FormatResultsTest(unittest.TestCase):
    def test_labels_each_result_as_a_heading(self):
        out = proto.format_results([{"label": "read a.py", "lang": "python", "body": "x = 1"}])
        self.assertIn("## read a.py", out)
        self.assertIn("```python\nx = 1\n```", out)

    def test_results_are_separated(self):
        out = proto.format_results([
            {"label": "one", "lang": "", "body": "1"},
            {"label": "two", "lang": "", "body": "2"},
        ])
        self.assertIn("## one", out)
        self.assertIn("## two", out)
        self.assertLess(out.index("## one"), out.index("## two"))

    def test_a_body_containing_a_fence_is_wrapped_in_a_longer_one(self):
        out = proto.format_results([{"label": "readme", "lang": "", "body": "```\ncode\n```"}])
        self.assertIn("````\n```\ncode\n```\n````", out)

    def test_failed_op_is_rendered_as_an_error_not_a_fence(self):
        out = proto.format_results([{"label": "read secret", "error": "blocked: .env"}])
        self.assertIn("## read secret", out)
        self.assertIn("blocked: .env", out)
        self.assertNotIn("```", out)


class TruncateTest(unittest.TestCase):
    def test_short_text_is_untouched(self):
        body, note = proto.truncate("hello", 100)
        self.assertEqual(body, "hello")
        self.assertIsNone(note)

    def test_long_text_is_cut_and_the_cut_is_announced(self):
        body, note = proto.truncate("x" * 500, 100)
        self.assertEqual(len(body), 100)
        self.assertIsNotNone(note)
        self.assertIn("truncated", note)

    def test_note_reports_both_sizes_so_the_model_can_ask_for_more(self):
        body, note = proto.truncate("x" * 500, 100)
        self.assertIn("100", note)
        self.assertIn("500", note)



class BudgetTest(unittest.TestCase):
    """Caps how much workspace data one run may pour into the conversation."""

    def test_a_fresh_round_may_spend_the_per_round_cap(self):
        budget = proto.Budget(total=1000, per_round=300)
        budget.begin_round()
        self.assertEqual(budget.allowance(), 300)

    def test_charging_reduces_the_round_allowance(self):
        budget = proto.Budget(total=1000, per_round=300)
        budget.begin_round()
        budget.charge(120)
        self.assertEqual(budget.allowance(), 180)

    def test_a_new_round_restores_the_round_allowance(self):
        budget = proto.Budget(total=1000, per_round=300)
        budget.begin_round()
        budget.charge(300)
        budget.begin_round()
        self.assertEqual(budget.allowance(), 300)

    def test_the_run_total_still_caps_a_fresh_round(self):
        budget = proto.Budget(total=400, per_round=300)
        budget.begin_round()
        budget.charge(300)
        budget.begin_round()
        self.assertEqual(budget.allowance(), 100)

    def test_allowance_never_goes_negative(self):
        budget = proto.Budget(total=100, per_round=300)
        budget.begin_round()
        budget.charge(500)
        self.assertEqual(budget.allowance(), 0)

    def test_exhausted_once_the_run_total_is_gone(self):
        budget = proto.Budget(total=100, per_round=300)
        budget.begin_round()
        self.assertFalse(budget.exhausted())
        budget.charge(100)
        self.assertTrue(budget.exhausted())

    def test_low_warns_before_it_is_too_late(self):
        budget = proto.Budget(total=1000, per_round=1000)
        budget.begin_round()
        budget.charge(700)
        self.assertFalse(budget.low())
        budget.charge(150)
        self.assertTrue(budget.low())

    def test_spent_is_reported_for_the_log(self):
        budget = proto.Budget(total=1000, per_round=300)
        budget.begin_round()
        budget.charge(42)
        self.assertEqual(budget.spent, 42)

    def test_notice_names_the_numbers_so_the_model_can_adapt(self):
        budget = proto.Budget(total=1000, per_round=300)
        budget.begin_round()
        budget.charge(900)
        notice = budget.notice()
        self.assertIn("100", notice)
        self.assertIn("1000", notice)

    def test_no_notice_while_there_is_room(self):
        budget = proto.Budget(total=1000, per_round=300)
        budget.begin_round()
        budget.charge(10)
        self.assertIsNone(budget.notice())


class LenientJsonTest(unittest.TestCase):
    """Models put real newlines inside JSON strings. Bouncing those costs a round.

    Python's json is strict about control characters by default, so a regex
    pattern written across two lines used to fail the block outright - twice in
    a row ends the whole run.
    """

    def test_a_literal_newline_inside_a_string_is_accepted(self):
        block = '```c2c\n{"ops":[{"op":"search","pattern":"def fee(\nx)"}]}\n```'
        ops = proto.extract_ops(block)
        self.assertEqual(ops[0]["op"], "search")
        self.assertIn("\n", ops[0]["pattern"])

    def test_a_literal_tab_inside_a_string_is_accepted(self):
        block = '```c2c\n{"ops":[{"op":"search","pattern":"a\tb"}]}\n```'
        self.assertEqual(proto.extract_ops(block)[0]["pattern"], "a\tb")

    def test_genuinely_broken_json_still_raises(self):
        with self.assertRaises(proto.ProtocolError):
            proto.extract_ops('```c2c\n{"ops":[{"op":}]}\n```')

    def test_the_error_quotes_the_block_so_it_can_be_diagnosed(self):
        try:
            proto.extract_ops('```c2c\nWHAT-WENT-WRONG-HERE\n```')
        except proto.ProtocolError as exc:
            self.assertIn("WHAT-WENT-WRONG-HERE", str(exc))
        else:
            self.fail("expected ProtocolError")


if __name__ == "__main__":
    unittest.main()
