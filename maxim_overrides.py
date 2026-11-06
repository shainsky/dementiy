"""Поправки Максима к поведению бота: правила и примеры «как надо».

Хранятся в JSON-файле (DATA_DIR/overrides.json) и переживают перезапуск.
Попадают в системный промпт выше стилевых подсказок, но ниже правил
безопасности.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from dataclasses import asdict, dataclass

MAX_RULES = 30
MAX_EXAMPLES = 10
MAX_TEXT = 500
MAX_PENDING = 50


# Промпт агента, который ищет поправку в реплике Максима боту
EXTRACT_PROMPT = """Ты помогаешь настраивать бота, который пишет в групповом чате от имени Максима и в его стиле.
Ниже уже сохранённые поправки Максима (пронумерованы), недавний разговор, реплика бота (может быть пустой) и сообщение самого Максима, адресованное боту.
Если Максим ссылается на разговор («учти это», «запомни, как ты ответил»), возьми суть из недавнего разговора и сформулируй её как самостоятельное правило или пример, понятный без этого разговора.
Если Максим одобряет ответ бота и просит его учесть — сохрани содержание этого ответа как факт о Максиме, его мнение или предпочтение (например: «Ты предпочитаешь Gentoo: систему, собранную из исходников»), а не как правило о манере отвечать.
Реши, говорит ли Максим, как боту себя вести или как надо было ответить. Обычный разговор, шутка или вопрос по теме — это не поправка.
Если новая поправка уточняет или противоречит сохранённой, замени ту поправку (поле replaces). Если поправка уже сохранена в том же смысле — это не новая поправка.

Верни только JSON одной строкой, без пояснений:
{"kind": "none"} — поправки нет;
{"kind": "rule", "text": "..."} — общее указание боту; сформулируй его коротко, в повелительном наклонении, до 200 символов;
{"kind": "rule", "text": "...", "replaces": N} — то же, но вместо сохранённой поправки номер N;
{"kind": "forget", "replaces": N} — Максим просит забыть сохранённую поправку номер N;
{"kind": "example", "text": "..."} — Максим даёт конкретную формулировку, как надо было ответить вместо реплики бота; верни эту формулировку."""


@dataclass
class Correction:
    kind: str           # "rule" | "example" | "forget"
    text: str = ""
    replaces: str = ""  # текст сохранённого правила, которое меняем или убираем


def parse_correction(raw: str, rules: list[str] | None = None) -> Correction | None:
    """Ответ агента → Correction или None. replaces: номер в rules (с 1) → текст правила."""
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    kind, text = data.get("kind"), str(data.get("text") or "").strip()[:MAX_TEXT]
    rules = rules or []
    n = data.get("replaces")
    replaces = rules[n - 1] if isinstance(n, int) and 1 <= n <= len(rules) else ""
    if kind == "forget":
        return Correction("forget", replaces=replaces) if replaces else None
    if kind == "rule" and text:
        return Correction("rule", text, replaces)
    if kind == "example" and text:
        return Correction("example", text)
    return None


def _dump_atomic(path: str, data) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Overrides:
    def __init__(self, path: str):
        self.path = path
        self.rules: list[str] = []
        self.examples: list[dict] = []  # {"bot": плохой ответ, "better": как надо}
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        self.rules = list(data.get("rules", []))
        self.examples = list(data.get("examples", []))

    def _save(self):
        _dump_atomic(self.path, {"rules": self.rules, "examples": self.examples})

    def add_rule(self, text: str) -> int:
        """Добавляет правило, возвращает его номер (с 1)."""
        self.rules.append(text.strip()[:MAX_TEXT])
        del self.rules[:-MAX_RULES]
        self._save()
        return len(self.rules)

    def add_example(self, bot_text: str, better: str) -> int:
        self.examples.append({"bot": bot_text.strip()[:MAX_TEXT], "better": better.strip()[:MAX_TEXT]})
        del self.examples[:-MAX_EXAMPLES]
        self._save()
        return len(self.examples)

    def apply(self, c: Correction, bot_text: str = "") -> str:
        """Применяет одобренную поправку, возвращает текст для подтверждения в чате."""
        if c.kind == "example":
            self.add_example(bot_text, c.text)
            return f"Учёл: «{c.text}»"
        if c.replaces in self.rules:
            i = self.rules.index(c.replaces)
            if c.kind == "forget":
                del self.rules[i]
                self._save()
                return f"Забыл: «{c.replaces}»"
            self.rules[i] = c.text.strip()[:MAX_TEXT]
            self._save()
            return f"Учёл: «{c.text}»"
        if c.kind == "forget":
            return "Этой поправки уже нет."
        self.add_rule(c.text)  # заменяемое правило уже убрано — просто добавляем
        return f"Учёл: «{c.text}»"

    def numbered_rules(self) -> str:
        return "\n".join(f"{i}. {r}" for i, r in enumerate(self.rules, 1)) or "(нет)"

    def prompt_section(self) -> str:
        """Блок для системного промпта; пустая строка, если поправок нет."""
        if not self.rules and not self.examples:
            return ""
        parts = [
            "\n\n## Поправки от самого Максима",
            "Максим сам дал эти указания. Они важнее стилевых подсказок и профиля выше, "
            "но не отменяют раздел «Безопасность».",
        ]
        parts += [f"- {r}" for r in self.rules]
        if self.examples:
            parts.append("\nПримеры: так бот ответил неудачно, а так надо было бы.")
            for e in self.examples:
                parts.append(f"- Было: «{e['bot']}»\n  Надо: «{e['better']}»")
        return "\n".join(parts)


class PendingCorrections:
    """Предложенные, но ещё не одобренные поправки. Переживают перезапуск:
    кнопки в чате ссылаются на id, и старая кнопка не должна попасть в чужую поправку."""

    def __init__(self, path: str):
        self.path = path
        self.items: dict[str, dict] = {}
        try:
            with open(path, encoding="utf-8") as f:
                self.items = json.load(f)
        except FileNotFoundError:
            pass

    def add(self, c: Correction, bot_text: str) -> str:
        cid = secrets.token_hex(4)
        self.items[cid] = {**asdict(c), "bot": bot_text[:MAX_TEXT]}
        for old in list(self.items)[:-MAX_PENDING]:
            del self.items[old]
        _dump_atomic(self.path, self.items)
        return cid

    def pop(self, cid: str) -> tuple[Correction, str] | None:
        item = self.items.pop(cid, None)
        if item is None:
            return None
        _dump_atomic(self.path, self.items)
        bot_text = item.pop("bot", "")
        return Correction(**item), bot_text
