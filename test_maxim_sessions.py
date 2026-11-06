import asyncio
import os
import stat
import tempfile
import unittest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST")
os.environ["LLM_PROVIDER"] = "codex"

import maxim_bot  # noqa: E402
from maxim_sessions import ChatSessions, build_turn  # noqa: E402

# Фейковый codex: на новую сессию печатает thread_id, на resume — отвечает с номером сессии
FAKE_CODEX = """#!/bin/sh
args="$*"
while [ "$1" ]; do [ "$1" = -o ] && out="$2"; [ "$1" = resume ] && sid="$2"; shift; done
cat > "$out.prompt"
case "$args" in *--json*) echo '{"type":"thread.started","thread_id":"T1"}';; esac
if [ "$sid" ]; then echo "resumed $sid" > "$out"; else echo "new" > "$out"; fi
"""


class BuildTurnTests(unittest.TestCase):
    def test_turn_contains_new_messages_and_last_reply(self):
        turn = build_turn([{"from": "Женя", "text": "привет"}], "Дементий, как дела?")
        self.assertIn("[Женя]: привет", turn)
        self.assertIn("[Последняя реплика, на которую нужно ответить]: Дементий, как дела?", turn)
        self.assertIn("SKIP", turn)
        self.assertNotIn("Новые сообщения", build_turn([], "x"))

    def test_task_turn_replaces_reply(self):
        turn = build_turn([{"from": "Женя", "text": "привет"}], None, task="Напиши первым.")
        self.assertIn("Напиши первым.", turn)
        self.assertNotIn("Последняя реплика", turn)


class ChatSessionsTests(unittest.TestCase):
    def test_session_expires_after_max_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            s = ChatSessions(path, max_turns=2)
            s.update(-1, thread_id="T", turns=1)
            self.assertEqual(ChatSessions(path, 2).get(-1)["thread_id"], "T")
            s.update(-1, turns=2)
            self.assertIsNone(s.get(-1))


class SessionFlowTests(unittest.TestCase):
    def test_first_turn_starts_session_then_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "codex")
            with open(fake, "w") as f:
                f.write(FAKE_CODEX)
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
            maxim_bot.CODEX_BIN = fake
            maxim_bot.DATA_DIR = tmp
            maxim_bot.chat_sessions = ChatSessions(os.path.join(tmp, "s.json"))
            engine = maxim_bot.MaximResponseEngine()
            ctx = [{"from": "Женя", "text": "привет", "date": "2026-11-04T10:00:00"}]
            first = asyncio.run(engine._generate_session(-5, "как дела?", ctx, ""))
            second = asyncio.run(engine._generate_session(-5, "а сейчас?", ctx, ""))
        self.assertEqual(first, "new")
        self.assertEqual(second, "resumed T1")
        self.assertEqual(maxim_bot.chat_sessions.get(-5)["turns"], 2)


# Фейковый codex, который сохраняет аргументы и prompt рядом с файлом ответа
RECORDING_CODEX = """#!/bin/sh
echo "$*" > "$CODEX_LOG.args"
cat > "$CODEX_LOG.prompt"
while [ "$1" ]; do [ "$1" = -o ] && out="$2"; shift; done
echo '{"type":"thread.started","thread_id":"T9"}'
echo '{"kind": "none"}' > "$out"
"""


class ContextAndLinksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        fake = os.path.join(self.tmp.name, "codex")
        with open(fake, "w") as f:
            f.write(RECORDING_CODEX)
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        maxim_bot.CODEX_BIN = fake
        maxim_bot.DATA_DIR = self.tmp.name
        maxim_bot.chat_sessions = ChatSessions(os.path.join(self.tmp.name, "s.json"))
        os.environ["CODEX_LOG"] = os.path.join(self.tmp.name, "log")
        self.engine = maxim_bot.MaximResponseEngine()

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, kind):
        with open(os.environ["CODEX_LOG"] + "." + kind) as f:
            return f.read()

    def test_extractor_sees_recent_conversation(self):
        ctx = [{"from": "Максим", "text": "я за сборку из исходников"},
               {"from": maxim_bot.BOT_LABEL, "text": "Т.е. Gentoo."}]
        asyncio.run(self.engine.extract_correction("Т.е. Gentoo.", "Учти это", ctx))
        prompt = self.read("prompt")
        self.assertIn("[Максим]: я за сборку из исходников", prompt)
        self.assertIn("[Бот (ты)]: Т.е. Gentoo.", prompt)
        self.assertIn("Сохранённые поправки:", prompt)

    def test_link_enables_web_search(self):
        asyncio.run(self.engine.generate("Глянь https://t.me/s/chan/1", [], -7))
        self.assertIn('web_search="live"', self.read("args"))
        self.assertIn("открой её", self.read("prompt"))
        self.assertIn("browser_navigate", self.read("prompt"))
        asyncio.run(self.engine.generate("без ссылок", [], -8))
        self.assertIn('web_search="disabled"', self.read("args"))

    def test_session_does_not_resend_bot_messages(self):
        ctx = [{"from": "Женя", "text": "привет", "date": "2026-11-04T10:00:00"},
               {"from": maxim_bot.BOT_LABEL, "text": "здорово", "date": "2026-11-04T10:00:01", "bot": True}]
        asyncio.run(self.engine._generate_session(-9, "как дела?", ctx, ""))
        prompt = self.read("prompt")
        self.assertIn("[Женя]: привет", prompt)
        self.assertNotIn("здорово", prompt)


class DirectCapTests(unittest.TestCase):
    def test_direct_replies_are_capped_per_chat(self):
        limiter = maxim_bot.RateLimiter()
        for _ in range(maxim_bot.DIRECT_MAX_PER_10MIN):
            self.assertTrue(limiter.direct_ok(-1))
            limiter.record_direct(-1)
        self.assertFalse(limiter.direct_ok(-1))
        self.assertTrue(limiter.direct_ok(-2))


if __name__ == "__main__":
    unittest.main()
