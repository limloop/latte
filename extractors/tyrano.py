"""
TyranoScript .ks extractor

Особенности:
- Парсит весь файл как поток токенов 'tag'/'text', поэтому многострочные
  теги корректно становятся ОДНИМ токеном.
- Внутри тега учитывается вложенность `[` `]` (для индексов вида &arr[0]).
- Тег начинается только если после `[` идёт буква или '_', иначе это текст.
- '#' в начале строки — конвенция имени персонажа Tyrano (#Mom).
- '[name="X"]' и '[chara_show name="X"]' — тоже источники имени персонажа.
- '@'-команды и ';'-комментарии пропускаются.
"""

import os
import re
from pathlib import Path
from extractors.base import BaseExtractor


# ============================================================
# Регулярные выражения
# ============================================================

RE_ISCRIPT_BLOCK = re.compile(
    r'\[iscript\b[^\]]*\].*?\[endscript\]',
    re.DOTALL | re.IGNORECASE,
)
RE_HTML_BLOCK = re.compile(
    r'\[html\b[^\]]*\].*?\[endhtml\]',
    re.DOTALL | re.IGNORECASE,
)
# [name="Mom"] / [name='Mom']
RE_NAME_TAG = re.compile(r'\[name\s*=\s*["\']([^"\']*)["\']')
# [chara_show ... name="Mom" ...] (name может стоять в любом месте тега)
RE_CHARA_NAME = re.compile(
    r'\[chara_(?:show|hide|mod|color|focus)\b[^\]]*?\bname\s*=\s*["\']([^"\']*)["\']',
    re.IGNORECASE,
)

# Ключ-маркер для маскировки кодовых блоков в апплаере.
# Состоит только из управляющих символов и цифр, чтобы final_clean_tyrano
# его гарантированно отбрасывал как «не текст».
BLOCK_KEY_TPL = '\x00\x00{}\x00\x00'


# ============================================================
# Токенизатор
# ============================================================

def tokenize_ks(text: str) -> list:
    """
    Разбить ВЕСЬ .ks файл на токены 'tag'/'text'.

    Гарантии:
    - Многострочный тег = один tag-токен.
    - `[0]`, `[i]`, `[some_index]` вне тега — часть текста.
    - `]` внутри кавычек не закрывает тег.
    - Вложенные `[ ... ]` внутри тега (в атрибутах) учитываются по глубине.

    Returns: [{'type': 'tag'|'text', 'content': str}, ...]
    """
    tokens = []
    buf = []
    in_tag = False
    in_dq = False       # внутри двойных кавычек
    in_sq = False       # внутри одинарных кавычек
    bracket_depth = 0   # глубина вложенных [ ] внутри тега
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        if in_tag:
            buf.append(ch)

            # Экранирование
            if ch == '\\' and i + 1 < n:
                buf.append(text[i + 1])
                i += 2
                continue

            # Кавычки (не влияют друг на друга)
            if ch == '"' and not in_sq:
                in_dq = not in_dq
            elif ch == "'" and not in_dq:
                in_sq = not in_sq
            elif not in_dq and not in_sq:
                if ch == '[':
                    bracket_depth += 1
                elif ch == ']':
                    if bracket_depth > 0:
                        bracket_depth -= 1
                    else:
                        tokens.append({'type': 'tag', 'content': ''.join(buf)})
                        buf = []
                        in_tag = False
                        in_dq = False
                        in_sq = False
                        bracket_depth = 0
        else:
            if ch == '[':
                nxt = text[i + 1] if i + 1 < n else ''
                # Тег начинается только с буквы или '_'
                if nxt.isalpha() or nxt == '_':
                    if buf:
                        tokens.append({'type': 'text', 'content': ''.join(buf)})
                        buf = []
                    in_tag = True
                    bracket_depth = 0
                    buf = ['[']
                else:
                    buf.append(ch)
            else:
                buf.append(ch)

        i += 1

    if buf:
        tokens.append({
            'type': 'tag' if in_tag else 'text',
            'content': ''.join(buf),
        })

    return tokens


# ============================================================
# Хелперы обработки строк
# ============================================================

def is_skippable_line(line: str) -> bool:
    """Строка целиком пропускается (комментарий / метка / @-команда)."""
    s = line.strip()
    if not s:
        return True
    if s.startswith(';'):    # комментарий
        return True
    if s.startswith('*'):    # метка *label
        return True
    if s.startswith('@'):    # @jump / @image / @call и пр.
        return True
    return False


def final_clean_tyrano(text: str) -> str:
    """
    Очистка текстового фрагмента для БД.
    Отбрасывает мусор: пустые, без букв, из одного повторяющегося символа.
    """
    text = text.strip()
    if not text:
        return ''

    # Схлопываем множественные пробелы и переводы строк
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return ''

    # Строка из одного повторяющегося символа (====, ....)
    if len(text) >= 3 and len(set(text)) == 1:
        return ''

    # Нет ни одной буквы (только цифры / пунктуация / управляющие символы)
    if not re.search(r'[^\W\d_]', text, re.UNICODE):
        return ''

    return text


def compute_context(filename: str, char_name: str) -> str:
    """
    Контекст для БД и AI-переводчика.
    Формат: 'filename|character' или 'filename'.
    """
    if char_name:
        return f'{filename}|{char_name}'
    return filename or ''


def update_character_from_tag(tag_content: str, current: str) -> str:
    """Обновить имя персонажа по содержимому tag-токена."""
    m = RE_NAME_TAG.search(tag_content)
    if m and m.group(1):
        return m.group(1).strip()
    m = RE_CHARA_NAME.search(tag_content)
    if m and m.group(1):
        return m.group(1).strip()
    return current


# ============================================================
# Экстрактор
# ============================================================

class TyranoExtractor(BaseExtractor):
    """Extract translatable strings from TyranoScript .ks files"""

    def run(self, source_dir: str = None, old_dir: str = None, **kwargs) -> int:
        source_dir = source_dir or self.config['paths']['new']

        ks_files = self._find_ks(source_dir)
        if not ks_files:
            print('No .ks files found')
            return 0

        entries = []
        for fp in ks_files:
            print(f'Parsing: {fp.name}')
            file_entries = self._parse_file(str(fp), filename=fp.stem)
            print(f'  Found {len(file_entries)} strings')
            entries.extend(file_entries)

        print(f'Total: {len(entries)} strings')

        # Дедупликация по (original, context)
        seen = set()
        normalized = []
        for e in entries:
            key = f"{e['original']}|{e['context']}"
            if key not in seen:
                seen.add(key)
                normalized.append(self._entry(
                    original=e['original'],
                    translation=e.get('translation'),
                    context=e.get('context', ''),
                ))

        count = self.db.insert_batch(normalized)
        print(f'Stored: {count} entries')
        s = self.db.stats()
        print(f"Total: {s['total']}, Translated: {s['translated']}")
        return count

    # --------------------------------------------------------
    # Внутреннее
    # --------------------------------------------------------

    def _find_ks(self, path: str) -> list:
        p = Path(path)
        if p.is_file() and p.suffix == '.ks':
            return [p]
        if p.is_dir():
            return sorted(p.rglob('*.ks'))
        return []

    def _parse_file(self, path: str, filename: str = '') -> list:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Вырезаем блоки чистого кода (не текст)
        content = RE_ISCRIPT_BLOCK.sub('', content)
        content = RE_HTML_BLOCK.sub('', content)

        tokens = tokenize_ks(content)
        entries = []
        current_char = ''

        for tok in tokens:
            if tok['type'] == 'tag':
                current_char = update_character_from_tag(tok['content'], current_char)
                continue

            # Текстовый токен: может содержать несколько строк
            for line in tok['content'].split('\n'):
                stripped = line.strip()
                if not stripped:
                    continue

                # '#Name' — конвенция имени персонажа в Tyrano
                if stripped.startswith('#'):
                    name = stripped[1:].strip()
                    if name:
                        current_char = name
                    continue

                if is_skippable_line(stripped):
                    continue

                cleaned = final_clean_tyrano(stripped)
                if cleaned:
                    entries.append(self._entry(
                        original=cleaned,
                        translation='',
                        context=compute_context(filename, current_char),
                    ))

        return entries