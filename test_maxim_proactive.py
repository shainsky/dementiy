import os
import random
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from maxim_proactive import ProactiveState, build_task, clean_post, next_post_time

TZ = ZoneInfo("Europe/Paris")


class ScheduleTests(unittest.TestCase):
    def test_always_inside_active_hours_and_after_min(self):
        rng = random.Random(1)
        now = datetime(2026, 11, 4, 21, 30, tzinfo=TZ)
        for _ in range(500):
            t = next_post_time(now, 12, 48, 10, 22, rng)
            self.assertTrue(10 <= t.hour < 22, t)
            self.assertGreaterEqual((t - now).total_seconds(), 12 * 3600 - 1)

    def test_night_time_moves_to_next_window(self):
        rng = random.Random(2)
        now = datetime(2026, 11, 4, 12, 0, tzinfo=TZ)
        t = next_post_time(now, 13, 13, 10, 22, rng)  # 01:00 следующего дня
        self.assertEqual(t.date(), datetime(2026, 11, 5).date())
        self.assertTrue(10 <= t.hour < 22)


class PostTests(unittest.TestCase):
    def test_clean_post(self):
        self.assertIsNone(clean_post("SKIP"))
        self.assertIsNone(clean_post("Интересно, но без ссылки"))
        self.assertEqual(clean_post("  Т.е. теперь так.\nhttps://x.org  "), "Т.е. теперь так.\nhttps://x.org")
        self.assertEqual(clean_post("Вот.\n[x 1.0](https://pypi.org/x/)"), "Вот.\n[x 1.0](https://pypi.org/x/)")

    def test_task_lists_recent_posts(self):
        self.assertIn("Не повторяй", build_task(["про Codex"]))
        self.assertNotIn("Не повторяй", build_task([]))

    def test_task_uses_chat_topics(self):
        self.assertIn("Тема должна быть интересна многим", build_task([]))
        self.assertIn("выбирай из них: баня, книги.", build_task([], "баня, книги"))
        self.assertNotIn("заданы владельцем", build_task([]))

    def test_state_per_chat_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "proactive.json")
            s = ProactiveState(path)
            s.remember_chat(-100123)
            s.set_next(-100123, datetime(2026, 11, 5, 15, 0, tzinfo=TZ))
            s.set_next(-100456, datetime(2026, 11, 6, 11, 0, tzinfo=TZ))
            s.add_post(-100123, "пост")
            again = ProactiveState(path)
            self.assertEqual(again.chats, {-100123})
            self.assertEqual(again.next_at[-100456], datetime(2026, 11, 6, 11, 0, tzinfo=TZ))
            self.assertEqual(again.recent, {-100123: ["пост"]})

    def test_old_single_schedule_format_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "proactive.json")
            with open(path, "w") as f:
                f.write('{"next_at": "2026-11-05T21:55:00+01:00", "chats": [-1], "recent": []}')
            s = ProactiveState(path)
            self.assertEqual((s.chats, s.next_at, s.recent), ({-1}, {}, {}))


if __name__ == "__main__":
    unittest.main()
