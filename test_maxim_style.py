import random
import unittest

from maxim_style import (
    BASE_SYSTEM_PROMPT,
    PHRASE_DICTIONARY,
    build_system_prompt,
    choose_phrase_cue,
    matching_phrase_cues,
)


class MaximStyleTests(unittest.TestCase):
    def test_no_cue_when_probability_is_zero(self):
        self.assertIsNone(choose_phrase_cue("У меня опять глючит VS Code", 0.0))

    def test_profanity_phrase_requires_profanity_context(self):
        clean = {cue.text for cue in matching_phrase_cues("У меня опять глючит программа")}
        rude = {cue.text for cue in matching_phrase_cues("Эта хуйня опять глючит")}
        self.assertNotIn("Та же хуйня.", clean)
        self.assertIn("Та же хуйня.", rude)

    def test_long_closing_is_documented_but_not_injected_as_a_reply(self):
        phrase = "Не буду вас больше мучить. Отвязываю от дыбы."
        dictionary_entry = next(cue for cue in PHRASE_DICTIONARY if cue.text == phrase)
        self.assertFalse(dictionary_entry.selectable)
        self.assertNotIn(phrase, {cue.text for cue in matching_phrase_cues("Объяснение " * 60)})

    def test_recent_phrase_is_excluded(self):
        phrase = "На мой взгляд, ..."
        cues = matching_phrase_cues("Как думаешь, какой вариант лучше?", [phrase])
        self.assertNotIn(phrase, {cue.text for cue in cues})

    def test_prompt_contains_at_most_selected_cue(self):
        cue = choose_phrase_cue(
            "Как думаешь, какой вариант лучше?",
            probability=1.0,
            rng=random.Random(7),
        )
        self.assertIsNotNone(cue)
        prompt = build_system_prompt(cue)
        self.assertIn(cue.text, prompt)
        self.assertIn("не обязательное требование", prompt)
        self.assertTrue(prompt.startswith(BASE_SYSTEM_PROMPT))

    def test_invalid_probability_is_rejected(self):
        with self.assertRaises(ValueError):
            choose_phrase_cue("text", 1.1)


if __name__ == "__main__":
    unittest.main()
