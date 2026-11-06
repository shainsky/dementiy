"""Сессии Codex по чатам: у каждой группы и лички своя история разговора.

DATA_DIR/sessions.json: chat_id → thread_id сессии Codex, число ходов, время
последнего сообщения чата, которое уже видела сессия, и хэш поправок Максима,
которые ей уже переданы.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile

SESSION_PREAMBLE = """Это долгая сессия одного чата. Дальше в каждом ходе приходят новые сообщения чата с прошлого раза и последняя реплика, на которую нужно ответить. Иногда вместо реплики приходит задача самому написать первым. Свои прошлые ответы ты помнишь сам. На каждый ход верни только готовую реплику для чата или ровно SKIP."""


def overrides_hash(section: str) -> str:
    return hashlib.sha256(section.encode()).hexdigest()[:16]


def build_turn(
    new_messages: list[dict],
    user_message: str | None,
    hints: str = "",
    overrides_section: str = "",
    task: str | None = None,
) -> str:
    """Текст очередного хода сессии: ответ на реплику или задача (task)."""
    parts = []
    if overrides_section:
        parts.append(overrides_section.strip() + "\n(Это обновлённые поправки, они заменяют прежние.)")
    if new_messages:
        lines = "\n".join(f"[{m['from']}]: {m['text']}" for m in new_messages)
        parts.append(f"Новые сообщения в чате с прошлого хода:\n{lines}")
    if task:
        parts.append(task)
        return "\n\n".join(parts)
    parts.append(f"[Последняя реплика, на которую нужно ответить]: {user_message}")
    if hints.strip():
        parts.append(hints.strip())
    parts.append("Ответь одной репликой или верни SKIP. Не запускай команды и не читай файлы.")
    return "\n\n".join(parts)


class ChatSessions:
    def __init__(self, path: str, max_turns: int = 150):
        self.path = path
        self.max_turns = max_turns
        self.sessions: dict[str, dict] = {}
        try:
            with open(path, encoding="utf-8") as f:
                self.sessions = json.load(f)
        except FileNotFoundError:
            pass

    def get(self, chat_id: int) -> dict | None:
        """Живая сессия чата или None, если её нет или она исчерпала лимит ходов."""
        session = self.sessions.get(str(chat_id))
        if session and session.get("turns", 0) < self.max_turns:
            return session
        return None

    def update(self, chat_id: int, **fields):
        session = self.sessions.setdefault(str(chat_id), {})
        session.update(fields)
        self._save()

    def reset(self, chat_id: int):
        self.sessions.pop(str(chat_id), None)
        self._save()

    def _save(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.sessions, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
