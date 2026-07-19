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
    CHANNEL_ID              — ID канала (например, -1001234567890)
"""

import os
import re
import time
import random
import asyncio
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from collections import defaultdict

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")  # openai | claude
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# Rate limiting
COOLDOWN_SECONDS = 180        # 3 минуты между любыми ответами
PER_USER_COOLDOWN = 600       # 10 минут между ответами одному человеку
DAILY_LIMIT = 30              # максимум ответов в сутки
TRIGGER_THRESHOLD = 25        # минимальный score для срабатывания

# Случайность — имитация живого настроения
RANDOM_JITTER = 10            # ± разброс score (0 = отключено)
RANDOM_LONGSHOT = 0.03        # 3% шанс ответить, даже если score сильно ниже порога
RANDOM_SILENCE = 0.05         # 5% шанс промолчать, даже если score выше порога

# Top собеседники Максима (из анализа)
TOP_INTERLOCUTORS = {
    "баталов", "хомутов", "бабаев", "коробейников",
    "хряпов", "шредер", "тяглов", "головешкин"
}

# Технические ключевые слова
TECH_KEYWORDS = [
    "llm", "prompt", "claude", "gemini", "gpt", "chatgpt", "модель",
    "код", "python", "c++", "gstreamer", "venv", "контекст", "context",
    "токен", "token", "нейро", "ai", "ии", "чат", "chat", "bot", "бот",
    "embedding", "fine-tune", "prompt-engineering", "copilot", "cursor"
]

# ---------------------------------------------------------------------------
# Системный промпт
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Ты — Максим Дементьев, участник технического чата «share-your-prompt».
Ты общаешься с коллегами-технарями, которых давно знаешь.

## Твой стиль

Тон:
- Прямой, неформальный, но не грубый
- С самоиронией — можешь шутить над собой
- Эмоциональный, но без истерики
- Интеллектуальный юмор: культурные отсылки (Пушкин, Цой, советские фильмы)

Структура ответа:
- Начинай с «Да, ...» если согласен и развиваешь мысль
- Или с «кстати» если хочешь неожиданно повернуть тему
- 2-3 предложения, не больше
- Можешь закончить «...» — это приглашение к диалогу
- Используй «я не знаю» если сомневаешься
- Используй «на мой взгляд» для личного мнения
- Используй «на самом деле» для коррекции

Лексика:
- Смешивай русский с английскими техтерминами (LLM, prompt, context, model)
- Используй «Gemini» и «Claude» — это твои основные инструменты
- Можешь сказать «та же хуйня» если собеседник описывает проблему, которая и у тебя
- Можешь сказать «Не буду вас больше мучить» после длинного объяснения

Темы:
- LLM, промпт-инжиниринг, C++, GStreamer, Python, встраиваемые системы
- Ты активно экспериментируешь с разными LLM и делишься результатами
- Ты часто в формате: «Me: [промпт] / Gemini: [ответ]»
- Любишь философские наблюдения о технологиях и людях

Запреты:
- НЕ будь вежливым формально (никаких «Уважаемые коллеги», «Благодарю за вопрос»)
- НЕ пиши длинные ответы (коротко, по делу)
- НЕ используй смайлики часто (изредка 😉)
- НЕ будь токсичным — ты ироничный, но доброжелательный

Примеры твоих реальных ответов:
- «Да, кстати, я не знаю, они в футбол играют или в паспортный контроль...»
- «На самом деле в самом конце ответа чата жпт — самое главное: "Какую задачу решаете?"»
- «Та же хуйня.»
- «Братва, не стреляйте в друг друга!»
- «Не буду вас больше мучить. Отвязываю от дыбы.»

Ответь на сообщение ниже в своём стиле. Только ответ, без пояснений."""

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
                self.client = AsyncOpenAI(api_key=OPENAI_API_KEY)
                self._generate = self._generate_openai
            except ImportError:
                raise RuntimeError("pip install openai")

    async def _generate_openai(self, user_message: str, context: list[str]) -> str:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        # Добавляем последние 3 сообщения для контекста
        for ctx_msg in context[-3:]:
            messages.append({"role": "user", "content": f"[Другой участник]: {ctx_msg}"})
        messages.append({"role": "user", "content": f"[Сообщение, на которое надо ответить]: {user_message}"})

        response = await self.client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            max_tokens=150,
            temperature=0.8,
        )
        return response.choices[0].message.content.strip()

    async def _generate_claude(self, user_message: str, context: list[str]) -> str:
        context_block = "\n".join(f"[Другой участник]: {m}" for m in context[-3:])
        full_prompt = f"{SYSTEM_PROMPT}\n\nКонтекст:\n{context_block}\n\n[Сообщение, на которое надо ответить]: {user_message}"

        response = await self.client.messages.create(
            model=LLM_MODEL,
            max_tokens=150,
            temperature=0.8,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Контекст:\n{context_block}\n\n[Сообщение]: {user_message}"}],
        )
        return response.content[0].text.strip()

    async def generate(self, user_message: str, context: list[str] = None) -> str:
        """Генерирует ответ в стиле Максима."""
        if context is None:
            context = []
        return await self._generate(user_message, context)


# ---------------------------------------------------------------------------
# Контекст канала (хранит последние N сообщений)
# ---------------------------------------------------------------------------

class ChannelContext:
    def __init__(self, max_size: int = 50):
        self.messages: list[dict] = []  # [{text, from, date}]
        self.max_size = max_size

    def add(self, text: str, sender: str):
        self.messages.append({
            "text": text,
            "from": sender,
            "date": datetime.now().isoformat()
        })
        if len(self.messages) > self.max_size:
            self.messages = self.messages[-self.max_size:]

    def get_recent_texts(self, count: int = 5) -> list[str]:
        return [m["text"] for m in self.messages[-count:]]


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

# Для отслеживания состояния
bot_started = False


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
    await message.answer(
        f"📊 Статус:\n"
        f"Активен: {'да' if bot_started else 'нет'}\n"
        f"Ответов сегодня: {rate_limiter.daily_count}/{DAILY_LIMIT}\n"
        f"Контекст: {len(channel_context.messages)} сообщений\n"
        f"Порог триггера: {TRIGGER_THRESHOLD}"
    )


@dp.message(Command("debug"))
async def cmd_debug(message: types.Message):
    """Показывает, сработал бы триггер на последнее сообщение."""
    if not channel_context.messages:
        await message.answer("Нет сохранённых сообщений.")
        return
    last = channel_context.messages[-1]
    trigger = detect_trigger(last["text"], last["from"], 0)
    await message.answer(
        f"🔍 Последнее сообщение от {last['from']}:\n"
        f"Score: {trigger.score} (порог: {TRIGGER_THRESHOLD})\n"
        f"Ответил бы: {'✅ да' if trigger.will_respond else '❌ нет'}\n"
        f"Причины: {', '.join(trigger.reasons) if trigger.reasons else '—'}\n"
        f"Текст: {last['text'][:200]}"
    )


@dp.channel_post()
async def handle_channel_post(message: types.Message):
    """Обрабатывает каждое сообщение в канале."""
    global bot_started

    if not bot_started:
        return

    # Пропускаем служебные сообщения и свои ответы
    if not message.text and not message.caption:
        return
    if message.from_user and message.from_user.is_bot:
        return

    text = message.text or message.caption or ""
    sender = message.author_signature or (message.from_user.full_name if message.from_user else "Unknown")
    user_id = message.from_user.id if message.from_user else 0

    # Сохраняем в контекст
    channel_context.add(text, sender)

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
        context_texts = channel_context.get_recent_texts(5)
        response = await response_engine.generate(text, context_texts)
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
        await message.reply(response)
        rate_limiter.record_response(user_id)
        logger.info(f"[SENT] to={sender} trigger_score={trigger.score} response={response[:100]}")
    except Exception as e:
        logger.error(f"[SEND ERROR] {e}")


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
