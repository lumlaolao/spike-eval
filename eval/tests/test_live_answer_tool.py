#!/usr/bin/env python3
"""OPTIONAL live-model tests of the answer tool: a real model, a fixed decision, the real `answer` function.

Skipped unless LIVE_MODEL_TESTS=1. They send each decision to openrouter.ai (OPENROUTER_API_KEY and the
owner's egress opt-in OPENROUTER_ALLOW_EGRESS=1 are required; neither is set here). Cost: about one cent
for the whole file on GPT-6 Luna (6-8 short calls). LIVE_MODEL picks another model.

    LIVE_MODEL_TESTS=1 python3 -m unittest eval.test_live_answer_tool -v

What they check, on the wire, the way the seat sends it (eval/tooluse/answer_tools.json, the tool_choice of
AnswerTool.toolChoiceFor):
- every decision comes back as a call to `answer`, never as text;
- the option is one on the list, and the obvious one where there is one (lethal on board);
- a model that fills every optional field leaves `yield` at "none" when it casts a spell;
- free spells: with no land and the opponent's spell on the stack, Force of Will is answerable.
"""

import json
import os
import time
import unittest
import urllib.request
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # eval/
import evalpath  # noqa: E402,F401  (every eval script folder on sys.path)

EVAL = evalpath.EVAL
MODEL = os.environ.get("LIVE_MODEL", "openai/gpt-6-luna")
LIVE = os.environ.get("LIVE_MODEL_TESTS") == "1"
SYSTEM = ("You are playing Magic: The Gathering, Legacy format, as the player \"Seat\" against one opponent. "
          "Your goal is to win the game. The engine lists every legal option; every decision is answered by "
          "calling `answer`.")


def tool_choice_for(model):
    """AnswerTool.toolChoiceFor: "required" for OpenAI models, "auto" elsewhere."""
    env = os.environ.get("ANSWER_TOOL_CHOICE")
    if env in ("auto", "required"):
        return env
    return "required" if model.replace("openrouter:", "").startswith("openai/") else "auto"


def ask(decision, model=MODEL):
    body = {"model": model, "max_tokens": 4000, "usage": {"include": True},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": decision}],
            "tools": json.loads((EVAL / "tooluse" / "answer_tools.json").read_text()),
            "tool_choice": tool_choice_for(model)}
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"],
                                          "Content-Type": "application/json"})
    t = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=300).read())
    msg = r["choices"][0]["message"]
    calls = [(c["function"]["name"], json.loads(c["function"]["arguments"] or "{}")) for c in msg.get("tool_calls") or []]
    usage = r.get("usage") or {}
    return {"calls": calls, "text": msg.get("content") or "", "seconds": time.time() - t,
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            "cost": usage.get("cost") or 0.0}


LETHAL = ("== Turn 4 MAIN1 (active: Seat) | priority: you ==\nYOU [Seat] life 9\n"
          "  hand(2): Lightning Bolt @1 {R} (castable), Fireblast @2 (castable by an alternative cost)\n"
          "  lands: 3 untapped (Mountain x3)\nOPP [Rival] life 3 | hand 4 cards\n  board: Delver of Secrets 3/2 flying @7\n\n"
          "DECISION: You have priority (your main phase). Choose ONE action:\n"
          "  1) CAST Lightning Bolt @1 {R} [Lightning Bolt deals 3 damage to any target.]\n"
          "  2) CAST Fireblast @2 [Fireblast deals 4 damage to any target. You may sacrifice two Mountains rather than "
          "pay this spell's mana cost.]\n  3) PASS (do nothing)\n"
          "ANSWER: call `answer` with `option`: one number from the list.")

FREE_SPELL = ("== Turn 2 MAIN1 (active: Rival) | priority: you ==\nYOU [Seat] life 20\n"
              "  hand(3): Force of Will @1 (castable by an alternative cost), Brainstorm @2 {U}, Ponder @3 {U}\n"
              "  lands: 0 untapped / 0 total\nOPP [Rival] life 20 | hand 5 cards\n"
              "stack (top first): Show and Tell (Rival) [Each player may put an artifact, creature, enchantment, or "
              "land card from their hand onto the battlefield.]\n"
              "CAST THIS GAME: Rival: Lotus Petal, Show and Tell. Rival's hand held Emrakul, the Aeons Torn (revealed).\n\n"
              "DECISION: You have priority (instant-speed window, the opponent's spell is on the stack). Choose ONE action:\n"
              "  1) CAST Force of Will @1 (alternative cost) — cost: pay 1 life, exile a blue card from your hand "
              "[Counter target spell.]\n  2) PASS (do nothing)\n"
              "ANSWER: call `answer` with `option`: one number from the list.")


@unittest.skipUnless(LIVE, "live-model tests are optional: LIVE_MODEL_TESTS=1 (about one cent on Luna)")
class LiveAnswerTool(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if os.environ.get("OPENROUTER_ALLOW_EGRESS") != "1" or not os.environ.get("OPENROUTER_API_KEY"):
            raise unittest.SkipTest("needs OPENROUTER_API_KEY and the owner's OPENROUTER_ALLOW_EGRESS=1")
        cls.spent = 0.0

    @classmethod
    def tearDownClass(cls):
        print("\n[live] model %s, tool_choice %s, cost $%.4f" % (MODEL, tool_choice_for(MODEL), cls.spent))

    def answered(self, decision, n_options):
        r = ask(decision)
        type(self).spent += r["cost"]
        answers = [a for name, a in r["calls"] if name == "answer"]
        self.assertTrue(answers, "no `answer` call; text was %r" % r["text"][:200])
        opt = answers[0].get("option")
        self.assertIn(opt, range(1, n_options + 1), "option %r is not on the list" % opt)
        return answers[0], r

    def test_every_decision_is_a_call_with_a_legal_option(self):
        for _ in range(2):
            a, r = self.answered(LETHAL, 3)
            self.assertEqual(a["option"], 1, "lethal Lightning Bolt on board: %s" % a)

    def test_a_filled_in_yield_on_a_cast_is_none(self):
        a, _ = self.answered(LETHAL, 3)
        self.assertIn(a.get("yield", "none"), ("none", "", None), "a cast filled yield with %r" % a.get("yield"))

    def test_forcing_keeps_the_reasoning_where_it_is_used(self):
        if tool_choice_for(MODEL) != "required":
            self.skipTest("tool_choice is auto for %s" % MODEL)
        _, r = self.answered(LETHAL, 3)
        self.assertIsNotNone(r["reasoning_tokens"])

    def test_a_free_spell_is_answerable_with_no_mana(self):
        a, _ = self.answered(FREE_SPELL, 2)
        self.assertEqual(a["option"], 1, "Force of Will against Show and Tell with Emrakul revealed: %s" % a)


if __name__ == "__main__":
    unittest.main()
