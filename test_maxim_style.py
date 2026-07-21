import random
import unittest

from maxim_style import (
    BASE_SYSTEM_PROMPT,
    FILM_QUOTES,
    PHRASE_DICTIONARY,
    build_system_prompt,
    choose_film_cues,
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
        self.assertNotIn(
            phrase, {cue.text for cue in matching_phrase_cues("Объяснение " * 60)}
        )

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

    # ── film quotes ──

    def test_film_cues_empty_when_probability_zero(self):
        result = choose_film_cues(probability=0.0, rng=random.Random(42))
        self.assertEqual(result, [])

    def test_film_cues_guaranteed_with_probability_one(self):
        result = choose_film_cues(probability=1.0, count=3, rng=random.Random(7))
        self.assertIn(len(result), (2, 3))
        self.assertEqual(len({q.text for q in result}), len(result), "duplicate quotes")

    def test_film_cues_excluded_are_not_returned(self):
        excluded = FILM_QUOTES[0].text
        for _ in range(50):
            result = choose_film_cues(
                probability=1.0, count=3, excluded=[excluded], rng=random.Random(123)
            )
            self.assertNotIn(excluded, {q.text for q in result})

    def test_prompt_with_film_cues_includes_quote_text_and_film(self):
        film_cues = list(FILM_QUOTES[:2])
        prompt = build_system_prompt(film_cues=film_cues)
        for q in film_cues:
            self.assertIn(q.text, prompt)
            self.assertIn(q.film, prompt)

    def test_prompt_with_both_cue_and_film_cues(self):
        cue = choose_phrase_cue(
            "Как думаешь, какой вариант лучше?",
            probability=1.0,
            rng=random.Random(7),
        )
        film_cues = list(FILM_QUOTES[:2])
        prompt = build_system_prompt(cue, film_cues)
        self.assertIn(cue.text, prompt)
        for q in film_cues:
            self.assertIn(q.text, prompt)

    def test_film_cues_invalid_probability_rejected(self):
        with self.assertRaises(ValueError):
            choose_film_cues(probability=1.5)

    def test_film_cue_count_respected(self):
        result = choose_film_cues(probability=1.0, count=2, rng=random.Random(99))
        self.assertEqual(len(result), 2)

    def test_film_quotes_have_all_three_films(self):
        films = {q.film for q in FILM_QUOTES}
        self.assertIn("Афоня", films)
        self.assertIn("Покровские ворота", films)
        self.assertIn("Д'Артаньян и три мушкетера", films)


if __name__ == "__main__":
    unittest.main()
