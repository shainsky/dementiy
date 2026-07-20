"""
🤖 Maxim Dementyev Telegram Bot
Читает сообщения в канале, определяет триггеры, отвечает в стиле Максима.

Требования:
    pip install aiogram openai
    # или для Claude: pip install anthropic

Переменные окружения:
    TELEGRAM_BOT_TOKEN      — токен бота от @BotFather
    OPENAI_API_KEY          — ключ OpenAI (или ANTHROPIC_API_KEY)
    LLM_PROVIDER            — "openai" или "claude" (по умолчанию openai)
    LLM_MODEL               — модель (по умолчанию gpt-4o-mini)
    CHANNEL_IDS             — ID каналов/групп через запятую (например, -100123,-100456).
                            Оставьте пустым чтобы слушать ВСЕ группы, куда бот добавлен.
"""

import os
import re
import time
import random
import asyncio
import logging
from datetime import datetime
from dataclasses import dataclass, field

from maxim_style import build_system_prompt, choose_phrase_cue

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")  # для DeepSeek: https://api.deepseek.com
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")  # openai | claude
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
# CHANNEL_IDS: список через запятую. Пустая строка = слушать все группы.
_CHANNEL_IDS_RAW = os.getenv("CHANNEL_IDS", "")
CHANNEL_IDS: set[int] = set()
if _CHANNEL_IDS_RAW.strip():
    try:
        CHANNEL_IDS = {int(x.strip()) for x in _CHANNEL_IDS_RAW.split(",") if x.strip()}
    except ValueError:
        raise RuntimeError(f"CHANNEL_IDS содержит некорректные значения: {_CHANNEL_IDS_RAW}")

# Rate limiting (отключено для отладки)
COOLDOWN_SECONDS = 0          # 3 минуты между любыми ответами
PER_USER_COOLDOWN = 0         # 10 минут между ответами одному человеку
DAILY_LIMIT = 999             # максимум ответов в сутки
TRIGGER_THRESHOLD = 25        # минимальный score для срабатывания

# Случайность — имитация живого настроения
RANDOM_JITTER = 10            # ± разброс score (0 = отключено)
RANDOM_LONGSHOT = 0.03        # 3% шанс ответить, даже если score сильно ниже порога
RANDOM_SILENCE = 0.05         # 5% шанс промолчать, даже если score выше порога
STYLE_CUE_PROBABILITY = float(os.getenv("STYLE_CUE_PROBABILITY", "0.20"))
if not 0.0 <= STYLE_CUE_PROBABILITY <= 1.0:
    raise RuntimeError("STYLE_CUE_PROBABILITY должна быть в диапазоне 0..1")
BOT_AUTO_START = os.getenv("BOT_AUTO_START", "1").lower() not in {"0", "false", "no"}

# Top собеседники Максима (из анализа)
TOP_INTERLOCUTORS = {
    "баталов", "хомутов", "бабаев", "коробейников",
    "хряпов", "шредер", "тяглов", "головешкин"
}

# Технические ключевые слова
TECH_KEYWORDS = [
    "llm", "prompt", "промпт", "claude", "gemini", "deepseek", "gpt",
    "chatgpt", "модель", "нейросет", "агент", "mcp", "rag", "embedding",
    "контекст", "context", "токен", "token", "ai", "ии", "чат", "chat",
    "код", "python", "питон", "c++", "си++", "gstreamer", "pipewire",
    "linux", "wayland", "компилят", "ассемблер", "микроконтроллер",
    "playwright", "vs code", "vscode", "copilot", "cursor", "windsurf",
    "git", "github", "api", "бот", "bot", "алгоритм", "отлад", "тест"
]

# ---------------------------------------------------------------------------
# Детектор триггеров
# ---------------------------------------------------------------------------

@dataclass
class TriggerResult:
    score: int
    will_respond: bool
    reasons: list[str] = field(default_factory=list)


def detect_trigger(text: str, sender_name: str, sender_id: int) -> TriggerResult:
    """Оценивает, ответил бы Максим на это сообщение.

    Правила дают базовый score. Затем три фактора случайности:
    - JITTER: ±N к score (настроение)
    - LONGSHOT: маленький шанс ответить вопреки низкому score (озарение)
    - SILENCE: маленький шанс промолчать вопреки высокому score (занят/не в духе)"""
    score = 0
    reasons = []
    text_lower = text.lower()

    # 1. Вопрос (+35)
    if "?" in text:
        score += 35
        reasons.append("вопрос")

    # 2. Техническая тема (+25)
    tech_matches = [kw for kw in TECH_KEYWORDS if kw in text_lower]
    if tech_matches:
        score += 25
        reasons.append(f"техтема: {', '.join(tech_matches[:3])}")

    # 3. Юмор/эмодзи (+15)
    if re.search(r'(\)\)\)|😂|🤣|😉|хаха|ахах|lol)', text_lower):
        score += 15
        reasons.append("юмор")

    # 4. Прямое обращение к Максиму (+30)
    if re.search(r'(@максим|максим\b|макс\b)', text_lower):
        score += 30
        reasons.append("обращение")

    # 5. Длинное сообщение (+10)
    if len(text) > 300:
        score += 10
        reasons.append("длинное")

    # 6. Проблема/ошибка (+20)
    if re.search(r'(проблем|ошибк|не работает|не могу|help|как сделать|не получается)', text_lower):
        score += 20
        reasons.append("проблема")

    # 7. Топ-собеседник (+10)
    sender_lower = sender_name.lower()
    if any(interlocutor in sender_lower for interlocutor in TOP_INTERLOCUTORS):
        score += 10
        reasons.append("топ-собеседник")

    # 8. Базовая реплика (+5) — фон
    score += 5

    # ---- Факторы случайности ----

    # Jitter: ± разброс (имитация настроения)
    jitter = random.randint(-RANDOM_JITTER, RANDOM_JITTER)
    if jitter != 0:
        reasons.append(f"jitter {jitter:+d}")
    score += jitter

    # Longshot: редкий шанс ответить, даже если score ниже порога
    longshot_triggered = False
    if score < TRIGGER_THRESHOLD and random.random() < RANDOM_LONGSHOT:
        score = TRIGGER_THRESHOLD + 1  # перебрасываем через порог
        longshot_triggered = True
        reasons.append("лонгшот")

    # Silence: редкий шанс промолчать, даже если score выше порога
    silence_triggered = False
    if score >= TRIGGER_THRESHOLD and random.random() < RANDOM_SILENCE:
        silence_triggered = True
        reasons.append("тишина")

    will_respond = (score >= TRIGGER_THRESHOLD or longshot_triggered) and not silence_triggered
    return TriggerResult(score=score, will_respond=will_respond, reasons=reasons)


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self):
        self.last_response: float = 0.0          # timestamp последнего ответа
        self.user_last_response: dict[int, float] = {}  # user_id → timestamp
        self.daily_count: int = 0
        self.day_start: str = datetime.now().strftime("%Y-%m-%d")

    def _reset_daily(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self.day_start:
            self.daily_count = 0
            self.day_start = today

    def can_respond(self, user_id: int) -> tuple[bool, str]:
        """Проверяет, можно ли ответить. Возвращает (разрешено, причина)."""
        self._reset_daily()
        now = time.time()

        if self.daily_count >= DAILY_LIMIT:
            return False, f"дневной лимит ({DAILY_LIMIT})"

        if now - self.last_response < COOLDOWN_SECONDS:
            remaining = int(COOLDOWN_SECONDS - (now - self.last_response))
            return False, f"кулдаун ({remaining}с)"

        if user_id in self.user_last_response:
            if now - self.user_last_response[user_id] < PER_USER_COOLDOWN:
                remaining = int(PER_USER_COOLDOWN - (now - self.user_last_response[user_id]))
                return False, f"персональный кулдаун ({remaining}с)"

        return True, "ok"

    def record_response(self, user_id: int):
        now = time.time()
        self.last_response = now
        self.user_last_response[user_id] = now
        self.daily_count += 1


# ---------------------------------------------------------------------------
# Response Engine (LLM)
# ---------------------------------------------------------------------------

class MaximResponseEngine:
    def __init__(self):
        self.client = None
        self._recent_cues: list[str] = []
        self._init_client()

    def _init_client(self):
        if LLM_PROVIDER == "claude":
            try:
                from anthropic import AsyncAnthropic
                self.client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
                self._generate = self._generate_claude
            except ImportError:
                raise RuntimeError("pip install anthropic")
        else:
            try:
                from openai import AsyncOpenAI
                client_kwargs = {"api_key": OPENAI_API_KEY}
                if OPENAI_BASE_URL:
                    client_kwargs["base_url"] = OPENAI_BASE_URL
                self.client = AsyncOpenAI(**client_kwargs)
                self._generate = self._generate_openai
            except ImportError:
                raise RuntimeError("pip install openai")

    @staticmethod
    def _context_lines(context: list[dict]) -> list[str]:
        return [f"[Контекст чата, {item['from']}]: {item['text']}" for item in context[-5:]]

    async def _generate_openai(
        self, user_message: str, context: list[dict], system_prompt: str
    ) -> str:
        messages = [{"role": "system", "content": system_prompt}]
        for context_line in self._context_lines(context):
            messages.append({"role": "user", "content": context_line})
        messages.append({
            "role": "user",
            "content": f"[Последняя реплика, на которую нужно ответить]: {user_message}",
        })

        response = await self.client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            max_tokens=220,
            temperature=0.75,
        )
        return response.choices[0].message.content.strip()

    async def _generate_claude(
        self, user_message: str, context: list[dict], system_prompt: str
    ) -> str:
        context_block = "\n".join(self._context_lines(context)) or "(нет)"
        response = await self.client.messages.create(
            model=LLM_MODEL,
            max_tokens=220,
            temperature=0.75,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": (
                    f"Контекст предыдущих реплик:\n{context_block}\n\n"
                    f"[Последняя реплика, на которую нужно ответить]: {user_message}"
                ),
            }],
        )
        return response.content[0].text.strip()

    async def generate(self, user_message: str, context: list[dict] | None = None) -> str:
        """Генерирует ответ и изредка предлагает модели одну уместную фразу."""
        context = context or []
        cue = choose_phrase_cue(
            user_message,
            probability=STYLE_CUE_PROBABILITY,
            excluded=self._recent_cues,
        )
        if cue:
            self._recent_cues.append(cue.text)
            self._recent_cues = self._recent_cues[-6:]
            logging.getLogger(__name__).info(
                "[STYLE-CUE] category=%s phrase=%r", cue.category, cue.text
            )
        return await self._generate(user_message, context, build_system_prompt(cue))


# ---------------------------------------------------------------------------
# Контекст канала (хранит последние N сообщений)
# ---------------------------------------------------------------------------

class ChannelContext:
    """Keeps independent conversation history for every channel/group."""

    def __init__(self, max_size: int = 50):
        self.messages_by_chat: dict[int, list[dict]] = {}
        self.last_message: dict | None = None
        self.max_size = max_size

    @property
    def total_size(self) -> int:
        return sum(len(messages) for messages in self.messages_by_chat.values())

    def add(self, chat_id: int, text: str, sender: str):
        message = {
            "chat_id": chat_id,
            "text": text,
            "from": sender,
            "date": datetime.now().isoformat(),
        }
        messages = self.messages_by_chat.setdefault(chat_id, [])
        messages.append(message)
        if len(messages) > self.max_size:
            del messages[:-self.max_size]
        self.last_message = message

    def get_recent(self, chat_id: int, count: int = 5) -> list[dict]:
        return self.messages_by_chat.get(chat_id, [])[-count:]


# ---------------------------------------------------------------------------
# Telegram Bot
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
rate_limiter = RateLimiter()
response_engine = MaximResponseEngine()
channel_context = ChannelContext()

# По умолчанию systemd-рестарт сразу возвращает бота в рабочее состояние.
bot_started = BOT_AUTO_START


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global bot_started
    bot_started = True
    await message.answer("🤖 Бот Максима запущен. Слушаю канал.")


@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    global bot_started
    bot_started = False
    await message.answer("🔴 Бот Максима остановлен.")


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    try:
        await message.answer(
            f"📊 Статус:\n"
            f"Активен: {'да' if bot_started else 'нет'}\n"
            f"Ответов сегодня: {rate_limiter.daily_count}/{DAILY_LIMIT}\n"
            f"Контекст: {channel_context.total_size} сообщений\n"
            f"Порог триггера: {TRIGGER_THRESHOLD}"
        )
    except Exception as e:
        logger.error(f"[STATUS ERROR] {e}")


@dp.message(Command("debug"))
async def cmd_debug(message: types.Message):
    """Показывает, сработал бы триггер на последнее сообщение."""
    if channel_context.last_message is None:
        await message.answer("Нет сохранённых сообщений.")
        return
    last = channel_context.last_message
    trigger = detect_trigger(last["text"], last["from"], 0)
    await message.answer(
        f"🔍 Последнее сообщение от {last['from']}:\n"
        f"Score: {trigger.score} (порог: {TRIGGER_THRESHOLD})\n"
        f"Ответил бы: {'✅ да' if trigger.will_respond else '❌ нет'}\n"
        f"Причины: {', '.join(trigger.reasons) if trigger.reasons else '—'}\n"
        f"Текст: {last['text'][:200]}"
    )


async def _process_message(message: types.Message):
    """Общая логика обработки сообщения (канал или группа)."""
    global bot_started

    if not bot_started:
        logger.info(f"[SKIP] bot_started=False — отправьте /start боту в личку")
        return

    # Пропускаем служебные сообщения и свои ответы
    if not message.text and not message.caption:
        logger.info(f"[SKIP] нет текста (text={message.text!r} caption={message.caption!r})")
        return
    if message.from_user and message.from_user.is_bot:
        logger.info(f"[SKIP] сообщение от бота: {message.from_user.full_name}")
        return

    text = message.text or message.caption or ""
    sender = message.author_signature or (message.from_user.full_name if message.from_user else "Unknown")
    user_id = message.from_user.id if message.from_user else 0
    chat_type = message.chat.type
    chat_id = message.chat.id

    # Белый список каналов/групп (если задан)
    if CHANNEL_IDS and chat_id not in CHANNEL_IDS:
        logger.info(f"[SKIP] чат {chat_id} не в CHANNEL_IDS (разрешены: {CHANNEL_IDS})")
        return

    logger.info(f"[MSG] chat={chat_type} chat_id={chat_id} from={sender} user_id={user_id} text={text[:80]!r}")

    # Берём только предыдущие реплики этого чата. Текущую добавляем после
    # снимка, чтобы LLM не увидела её дважды и чтобы разные чаты не смешивались.
    context_messages = channel_context.get_recent(chat_id, 5)
    channel_context.add(chat_id, text, sender)

    # Проверяем триггер
    trigger = detect_trigger(text, sender, user_id)
    logger.info(f"[TRIGGER] from={sender} score={trigger.score} respond={trigger.will_respond} reasons={trigger.reasons}")

    if not trigger.will_respond:
        return

    # Проверяем rate limit
    can_respond, reason = rate_limiter.can_respond(user_id)
    if not can_respond:
        logger.info(f"[RATE-LIMIT] {reason} for {sender}")
        return

    # Генерируем ответ
    try:
        response = await response_engine.generate(text, context_messages)
    except Exception as e:
        logger.error(f"[LLM ERROR] {e}")
        return

    # Проверка качества ответа: не отправляем пустые или формальные ответы
    # на слабые триггеры
    if trigger.score < 30 and len(response) < 10:
        logger.info(f"[QUALITY] Ответ слишком короткий для слабого триггера, пропускаем")
        return

    if not response.strip():
        return

    # Отправляем
    try:
        # Ответ LLM — обычный текст. Не даём угловым скобкам из C++/HTML
        # превращаться в Telegram entities и ломать отправку.
        await message.reply(response, parse_mode=None)
        rate_limiter.record_response(user_id)
        logger.info(f"[SENT] to={sender} trigger_score={trigger.score} response={response[:100]}")
    except Exception as e:
        logger.error(f"[SEND ERROR] {e}")


@dp.channel_post()
async def handle_channel_post(message: types.Message):
    """Обрабатывает сообщения в канале."""
    logger.info(f"[HANDLER] channel_post вызван")
    await _process_message(message)


@dp.message()
async def handle_group_message(message: types.Message):
    """Обрабатывает не-командные сообщения в группах/супергруппах.
    Срабатывает только после того, как все Command-хендлеры не подошли."""
    # Не обрабатываем личные сообщения (чтобы не мешать /start /status /debug)
    if message.chat.type == "private":
        logger.info(f"[HANDLER] личное сообщение пропущено: {message.text!r}")
        return
    logger.info(f"[HANDLER] group_message: chat={message.chat.type} text={message.text!r}")
    await _process_message(message)


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

async def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан!")
        return
    logger.info("🤖 Бот Максима Дементьева запускается...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
