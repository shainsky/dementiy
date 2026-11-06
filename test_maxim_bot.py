import asyncio
import os
import stat
import tempfile
from types import SimpleNamespace
import unittest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST")
os.environ["LLM_PROVIDER"] = "codex"

import maxim_bot  # noqa: E402


class DirectAddressTests(unittest.TestCase):
    def test_username_mention_ignores_case(self):
        self.assertTrue(maxim_bot.is_bot_mentioned("эй @Dementiy_Bot, глянь", "dementiy_bot"))
        self.assertFalse(maxim_bot.is_bot_mentioned("@dementiy_bot2 глянь", "dementiy_bot"))
        self.assertFalse(maxim_bot.is_bot_mentioned("@dementiy_bot", None))

    def test_dementiy_all_cases(self):
        for text in ("Дементий, ты где?", "спроси дементия", "@дементию", "с Дементием", "о дементии"):
            self.assertTrue(maxim_bot.DEMENTIY_RE.search(text), text)

    def test_maxim_is_not_direct_address(self):
        self.assertIsNone(maxim_bot.DEMENTIY_RE.search("Максим, Макс, глянь"))

    def test_direct_response_skips_cooldown(self):
        limiter = maxim_bot.RateLimiter()
        limiter.record_response(-1, 1, cooldown=False)
        self.assertEqual(limiter.can_respond(-1, 1), (True, "ok"))
        self.assertEqual(limiter.daily_count, {-1: 1})
        limiter.record_response(-1, 1)
        self.assertFalse(limiter.can_respond(-1, 1)[0])

    def test_cooldown_and_daily_limit_are_per_chat(self):
        limiter = maxim_bot.RateLimiter()
        limiter.record_response(-1, 1)
        self.assertTrue(limiter.can_respond(-2, 1)[0])
        self.assertTrue(limiter.can_respond(-2, 7)[0])
        for _ in range(maxim_bot.DAILY_LIMIT):
            limiter.record_response(-3, 1, cooldown=False)
        self.assertEqual(limiter.can_respond(-3, 2)[1], f"дневной лимит ({maxim_bot.DAILY_LIMIT})")
        self.assertTrue(limiter.can_respond(-2, 2)[0])


class OwnerTests(unittest.TestCase):
    def test_only_admin_ids_are_owners(self):
        maxim_bot.ADMIN_IDS = {42}
        owner = SimpleNamespace(from_user=SimpleNamespace(id=42))
        stranger = SimpleNamespace(from_user=SimpleNamespace(id=7))
        anonymous = SimpleNamespace(from_user=None)
        self.assertTrue(maxim_bot.is_owner(owner))
        self.assertFalse(maxim_bot.is_owner(stranger))
        self.assertFalse(maxim_bot.is_owner(anonymous))


def _msg(**kw):
    base = dict(reply_to_message=None, quote=None, entities=None, caption_entities=None,
                text=None, caption=None, from_user=None, author_signature=None,
                is_topic_message=False, message_thread_id=None, message_id=1)
    base.update(kw)
    return SimpleNamespace(**base)


class ReplyBlockTests(unittest.TestCase):
    def test_reply_carries_author_text_and_hidden_links(self):
        link = SimpleNamespace(type="text_link", url="https://smart-lab.ru/x")
        original = _msg(message_id=5, text="9 моделей мышления", entities=[link],
                        from_user=SimpleNamespace(id=7, full_name="Roman"))
        block = maxim_bot.reply_block(_msg(text="посмотри", reply_to_message=original))
        self.assertIn("[В ответ на сообщение Roman]: 9 моделей мышления", block)
        self.assertIn("https://smart-lab.ru/x", block)

    def test_selected_quote_wins_and_topic_root_is_ignored(self):
        original = _msg(message_id=5, text="длинный текст", from_user=SimpleNamespace(id=7, full_name="R"))
        quoted = _msg(reply_to_message=original, quote=SimpleNamespace(text="кусок"))
        self.assertIn("]: кусок", maxim_bot.reply_block(quoted))
        topic = _msg(reply_to_message=original, is_topic_message=True, message_thread_id=5)
        self.assertEqual(maxim_bot.reply_block(topic), "")


class MediaAndBoostTests(unittest.TestCase):
    def test_unseen_media_is_marked_in_reply(self):
        photo = _msg(message_id=5, photo=[object()], from_user=SimpleNamespace(id=7, full_name="R"))
        self.assertIn("[фото, ты его не видишь]", maxim_bot.reply_block(_msg(reply_to_message=photo)))
        self.assertEqual(maxim_bot.media_kind(_msg(text="просто текст")), "")

    def test_user_boost_shifts_score(self):
        names = ("USER_TRIGGER_BOOST", "RANDOM_JITTER", "RANDOM_LONGSHOT", "RANDOM_SILENCE")
        saved = {n: getattr(maxim_bot, n) for n in names}
        self.addCleanup(lambda: [setattr(maxim_bot, n, v) for n, v in saved.items()])
        maxim_bot.USER_TRIGGER_BOOST = {977070655: 10}
        maxim_bot.RANDOM_JITTER = maxim_bot.RANDOM_LONGSHOT = maxim_bot.RANDOM_SILENCE = 0
        boosted = maxim_bot.detect_trigger("ок", "Hawaiian Boy", 977070655)
        plain = maxim_bot.detect_trigger("ок", "Кто-то", 1)
        self.assertIn("оверрайд +10", boosted.reasons)
        self.assertEqual(boosted.score - plain.score, 10)


class CodexProviderTests(unittest.TestCase):
    def test_codex_exec_answer_is_read_from_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "codex")
            with open(fake, "w") as f:
                f.write(
                    "#!/bin/sh\n"
                    'while [ "$1" ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
                    'grep -q "Последняя реплика" && echo "  Т.е. работает.  " > "$out"\n'
                )
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
            maxim_bot.CODEX_BIN = fake
            engine = maxim_bot.MaximResponseEngine()
            answer = asyncio.run(engine._generate_codex("как дела?", [], "system"))
        self.assertEqual(answer, "Т.е. работает.")

    def test_playwright_mcp_only_with_web_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "codex")
            with open(fake, "w") as f:
                f.write(
                    "#!/bin/sh\n"
                    'all="$*"; while [ "$1" ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
                    'case "$all" in *mcp_servers.playwright.command*) m=mcp;; *) m=none;; esac\n'
                    'grep -q "browser_navigate" && p=hint || p=nohint\n'
                    'echo "$m $p" > "$out"\n'
                )
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
            maxim_bot.CODEX_BIN = fake
            engine = maxim_bot.MaximResponseEngine()
            with_web = asyncio.run(engine._codex_exec("x", web_search=True))
            without = asyncio.run(engine._codex_exec("x"))
        self.assertEqual(with_web, "mcp hint")
        self.assertEqual(without, "none nohint")


if __name__ == "__main__":
    unittest.main()
