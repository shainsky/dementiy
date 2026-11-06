"""Сообщения бота первым: плавающее расписание и состояние.

У каждой группы своё расписание. Состояние (DATA_DIR/proactive.json) переживает
перезапуск: группы, где бот видел сообщения, время следующего поста в каждой
и последние посты в каждой (чтобы не повторяться).
"""

from __future__ import annotations

import json
import os
import random
import tempfile
from datetime import datetime, timedelta

MAX_RECENT = 15

PROACTIVE_TASK = """Сейчас ты сам начинаешь разговор в чате, никто тебя не спрашивал.
Сначала пойми, что это за чат: о чём в нём обычно говорят и о чём говорили недавно (история этой сессии).
Тему выбирай под этот чат, на пересечении с интересами Максима. Интересы у него широкие: LLM и агенты, инструменты разработчика, C++/Python, Linux и мультимедиа, но и язык и перевод, наука, музыка, кино, книги, быт и жизнь во Франции.
Технику и ИИ бери, только если чат о технике или о ней там сейчас говорят. В чате на общие темы пиши об общем.
Тема должна быть интересна многим в чате: не цепляйся за одну случайную реплику и не повторяй уже обсуждённое.
Найди через web search одну свежую (за последние 7 дней) и действительно интересную находку по выбранной теме. Выбирай то, что вызывает мысль или спор, а не пресс-релиз.
Напиши сообщение в чат, как будто Максим сам делится находкой: 1–3 коротких предложения с его реакцией или мыслью, затем ссылка на источник отдельной строкой.
Без заголовков и другого оформления, без обращений вроде «Друзья» и без «Смотрите, что нашёл». Ссылку дай в виде [короткое название](url).
Если ничего стоящего не нашлось, верни ровно слово SKIP."""


def next_post_time(
    now: datetime,
    min_hours: float,
    max_hours: float,
    start_hour: int,
    end_hour: int,
    rng: random.Random | None = None,
) -> datetime:
    """Случайное время через min..max часов, внутри активных часов [start, end)."""
    rng = rng or random
    t = now + timedelta(hours=rng.uniform(min_hours, max_hours))
    if start_hour <= t.hour < end_hour:
        return t
    # Вне активных часов — случайный момент в ближайшем следующем окне
    day = t if t.hour < start_hour else t + timedelta(days=1)
    window_start = day.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    return window_start + timedelta(hours=rng.uniform(0, end_hour - start_hour))


def build_task(recent: list[str], topics: str = "") -> str:
    """topics — темы чата, заданные вручную (PROACTIVE_TOPICS); они важнее догадок по истории."""
    task = PROACTIVE_TASK
    if topics:
        task += f"\n\nТемы этого чата заданы владельцем, выбирай из них: {topics}."
    if recent:
        shown = "\n".join(f"- {p[:200]}" for p in recent[-MAX_RECENT:])
        task += f"\n\nНе повторяй темы своих прошлых сообщений в этом чате:\n{shown}"
    return task


def clean_post(raw: str) -> str | None:
    """None, если модель отказалась или в посте нет ссылки."""
    text = raw.strip()
    if not text or text.upper().startswith("SKIP") or "http" not in text:
        return None
    return text


class ProactiveState:
    def __init__(self, path: str):
        self.path = path
        self.chats: set[int] = set()
        self.next_at: dict[int, datetime] = {}
        self.recent: dict[int, list[str]] = {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        self.chats = set(data.get("chats", []))
        # Старый формат (одно общее расписание) просто отбрасываем
        if isinstance(data.get("next_at"), dict):
            self.next_at = {int(k): datetime.fromisoformat(v) for k, v in data["next_at"].items()}
        if isinstance(data.get("recent"), dict):
            self.recent = {int(k): list(v) for k, v in data["recent"].items()}

    def save(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({
                "chats": sorted(self.chats),
                "next_at": {str(k): v.isoformat() for k, v in self.next_at.items()},
                "recent": {str(k): v[-MAX_RECENT:] for k, v in self.recent.items()},
            }, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def remember_chat(self, chat_id: int):
        if chat_id not in self.chats:
            self.chats.add(chat_id)
            self.save()

    def set_next(self, chat_id: int, when: datetime):
        self.next_at[chat_id] = when
        self.save()

    def add_post(self, chat_id: int, text: str):
        self.recent[chat_id] = (self.recent.get(chat_id, []) + [text])[-MAX_RECENT:]
        self.save()
