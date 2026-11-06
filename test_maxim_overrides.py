import os
import tempfile
import unittest

from maxim_overrides import MAX_RULES, Correction, Overrides, PendingCorrections, parse_correction
from maxim_style import build_system_prompt


class OverridesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "overrides.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_rules_and_examples_survive_restart(self):
        o = Overrides(self.path)
        self.assertEqual(o.prompt_section(), "")
        self.assertEqual(o.add_rule("  не шути про Марсель "), 1)
        o.add_example("Отличный вопрос!", "Т.е. ты про context window?")
        again = Overrides(self.path)
        self.assertEqual(again.rules, ["не шути про Марсель"])
        self.assertEqual(again.examples[0]["better"], "Т.е. ты про context window?")
        self.assertIn("- не шути про Марсель", again.prompt_section())
        self.assertIn("Надо: «Т.е. ты про context window?»", again.prompt_section())

    def test_parse_correction(self):
        self.assertEqual(parse_correction('{"kind": "none"}'), None)
        self.assertEqual(parse_correction("не JSON"), None)
        self.assertEqual(parse_correction('{"kind": "rule", "text": ""}'), None)
        self.assertEqual(
            parse_correction('Вот: {"kind": "rule", "text": "Не шути про Марсель"}'),
            Correction("rule", "Не шути про Марсель"),
        )
        self.assertEqual(
            parse_correction('{"kind": "example", "text": "Т.е. ты про context?"}'),
            Correction("example", "Т.е. ты про context?"),
        )

    def test_parse_replaces_and_forget_by_number(self):
        rules = ["про Mint", "не шути"]
        self.assertEqual(
            parse_correction('{"kind": "rule", "text": "про Gentoo", "replaces": 1}', rules),
            Correction("rule", "про Gentoo", "про Mint"),
        )
        self.assertEqual(parse_correction('{"kind": "forget", "replaces": 2}', rules),
                         Correction("forget", replaces="не шути"))
        self.assertIsNone(parse_correction('{"kind": "forget", "replaces": 9}', rules))

    def test_apply_replaces_and_forgets(self):
        o = Overrides(self.path)
        o.add_rule("про Mint")
        o.add_rule("не шути")
        o.apply(Correction("rule", "про Gentoo", "про Mint"))
        self.assertEqual(o.rules, ["про Gentoo", "не шути"])
        o.apply(Correction("forget", replaces="не шути"))
        self.assertEqual(Overrides(self.path).rules, ["про Gentoo"])
        self.assertEqual(o.apply(Correction("forget", replaces="не шути")), "Этой поправки уже нет.")

    def test_pending_survive_restart_with_unique_ids(self):
        path = os.path.join(self.tmp.name, "pending.json")
        p = PendingCorrections(path)
        a = p.add(Correction("rule", "A"), "бот")
        b = p.add(Correction("rule", "B"), "")
        self.assertNotEqual(a, b)
        again = PendingCorrections(path)
        self.assertEqual(again.pop(a), (Correction("rule", "A"), "бот"))
        self.assertIsNone(PendingCorrections(path).pop(a))
        self.assertEqual(PendingCorrections(path).pop(b)[0].text, "B")

    def test_rules_are_capped(self):
        o = Overrides(self.path)
        for i in range(MAX_RULES + 5):
            o.add_rule(str(i))
        self.assertEqual(len(o.rules), MAX_RULES)
        self.assertEqual(o.rules[0], "5")

    def test_section_goes_before_final_instruction(self):
        prompt = build_system_prompt(overrides_section="\n\n## Поправки от самого Максима\n- x")
        self.assertLess(prompt.index("## Поправки"), prompt.index("Не объясняй наличие подсказок"))


if __name__ == "__main__":
    unittest.main()
