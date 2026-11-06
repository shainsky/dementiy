"""Markdown модели → HTML для Telegram (parse_mode=HTML).

MarkdownV2 требует экранировать почти всю пунктуацию, и обычный markdown от
модели Telegram отвергает. В HTML экранируются только < > &, поэтому
переводим сами: **жирный**, *курсив*, ~~зачёркнутый~~, `код`, блоки ```,
[текст](url), заголовки # → жирная строка.
"""

from __future__ import annotations

import html
import re

_FENCE_RE = re.compile(r"```([\w+-]*)\n?(.*?)```", re.S)
_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*|__(?!\s)(.+?)(?<!\s)__", re.S)
# Курсив только по границам слов, чтобы не ломать snake_case и 2*3*4
_ITALIC_RE = re.compile(
    r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])|(?<![\w_])_(?![\s_])(.+?)(?<![\s_])_(?![\w_])"
)
_STRIKE_RE = re.compile(r"~~(?!\s)(.+?)(?<!\s)~~")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*$", re.M)


def md_to_telegram_html(text: str) -> str:
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    def fence(m: re.Match) -> str:
        lang, body = m.group(1), html.escape(m.group(2).rstrip("\n"), quote=False)
        attr = f' class="language-{lang}"' if lang else ""
        return keep(f"<pre><code{attr}>{body}</code></pre>")

    text = _FENCE_RE.sub(fence, text)
    text = _CODE_RE.sub(lambda m: keep(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)
    text = _LINK_RE.sub(
        lambda m: keep(
            f'<a href="{html.escape(m.group(2))}">{html.escape(m.group(1), quote=False)}</a>'
        ),
        text,
    )
    text = html.escape(text, quote=False)
    text = _HEADING_RE.sub(r"<b>\1</b>", text)
    text = _BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _ITALIC_RE.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    text = _STRIKE_RE.sub(r"<s>\1</s>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
