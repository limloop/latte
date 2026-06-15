"""
Twine/SugarCube HTML extractor
"""

import os
import re
import html as html_module
from pathlib import Path
from extractors.base import BaseExtractor


# ============================================================
# Общие утилиты для обработки текста (используются экстрактором и апплаером)
# ============================================================

# Расширения файлов изображений
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg', '.ico', '.tiff', '.tif'}

# Пассажи, которые не нужно переводить
SKIP_PASSAGES = {
    'StoryInit', 'StoryReady', 'PassageReady', 'PassageDone',
    'PassageHeader', 'PassageFooter', 'StoryMenu', 'StoryBanner',
    'StoryCaption', 'StorySubtitle', 'StoryTitle', 'StoryAuthor'
}


def process_passage_text(inner: str) -> str:
    """
    Общая обработка текста пассажа.
    Используется и экстрактором и апплаером.
    """
    # 1. unescape HTML entities (двойной прогон)
    decoded = html_module.unescape(inner)
    decoded = html_module.unescape(decoded)
    
    # 2. Удаляем ВСЕ макросы <<...>> полностью
    decoded = re.sub(r'<<.*?>>', '\n', decoded)
    
    # 3. Удаляем HTML теги
    decoded = re.sub(r'<[^>]+>', '\n', decoded)
    
    return decoded


def skip_line(line: str) -> bool:
    """Проверить, нужно ли пропустить строку (мусор/разметка)"""
    line = line.strip()
    if not line:
        return True

    # img
    if line.startswith('img '):
        if line.endswith('}'):
            return True
        for ext in IMAGE_EXTENSIONS:
            if line.rstrip('"').endswith(ext):
                return True
        return True

    # span style
    if line.startswith('span '):
        parts = line.split()
        if len(parts) == 2 and parts[1] == 'style':
            return True

    # style
    if line.startswith('style ') or line == 'style':
        return True

    # set $ команды
    if re.match(r'set\s+\$', line):
        return True

    # Чистая переменная
    if re.match(r'^\$[a-zA-Z_]\w*$', line):
        return True

    return False


def extract_parts(line: str) -> list:
    """Извлекает текст вне ссылок и отображаемый текст из [[...]]"""
    results = []
    pattern = re.compile(r'\[\[(.+?)\]\]')
    last_end = 0

    for match in pattern.finditer(line):
        before = line[last_end:match.start()].strip()
        if before:
            results.append(before)

        inner = match.group(1).strip()
        if '->' in inner:
            display = inner.split('->')[0].strip()
        elif '|' in inner:
            display = inner.split('|')[0].strip()
        else:
            display = inner

        if display:
            results.append(display)

        last_end = match.end()

    after = line[last_end:].strip()
    if after:
        results.append(after)

    if not results:
        results.append(line)

    return results


def final_clean(text: str) -> str:
    """Финальная очистка строки от мусора"""
    if not text or not text.strip():
        return ''

    text = text.strip()

    # Парные символы с краёв (БЕЗ одиночных кавычек!)
    PAIRS = [
        ('"', '"'), ('„', '"'), ('"', '"'), ('«', '»'),
        ('(', ')'), ('[', ']'), ('{', '}'),
        ('*', '*'), ('_', '_'), ('`', '`'), ('~', '~'),
    ]

    changed = True
    while changed:
        changed = False
        for left, right in PAIRS:
            if text.startswith(left) and text.endswith(right):
                text = text[len(left):-len(right)].strip()
                changed = True

        for char in '/|\\#@.,:;':
            if text.startswith(char):
                text = text[1:].strip()
                changed = True
            if text.endswith(char):
                text = text[:-1].strip()
                changed = True

    if not text:
        return ''

    # Удаляем числа с +/- и >>/<< в начале и конце
    text = re.sub(r'^[\+\-\d\s>]+', '', text)
    text = re.sub(r'[\+\-\d\s<]+$', '', text)
    text = text.strip()

    # Строка из одного повторяющегося символа
    if len(text) >= 3 and len(set(text)) == 1:
        return ''

    if not text:
        return ''

    # Только число
    if re.match(r'^[\+\-\d\s\.\,\-]+$', text):
        return ''

    # Только пунктуация без букв
    if re.match(r'^[\s\W_]+$', text) and not re.search(r'[a-zA-Z]{3,}', text):
        return ''

    return text


def mask_links(text: str) -> tuple:
    """Заменить ссылки [[...]] на метки. Возвращает (текст, словарь_ссылок)"""
    links = {}
    counter = 0
    
    def replace_link(match):
        nonlocal counter
        inner = match.group(1).strip()
        
        if '->' in inner:
            display, target = inner.split('->', 1)
            display = display.strip()
            target = target.strip()
        elif '|' in inner:
            display, target = inner.split('|', 1)
            display = display.strip()
            target = target.strip()
        else:
            display = inner
            target = inner
        
        key = f'__LINK_{counter}__'
        links[key] = {
            'display': display, 
            'target': target
        }
        counter += 1
        return key
    
    result = re.sub(r'\[\[(.+?)\]\]', replace_link, text)
    return result, links


def restore_links(text: str, links: dict, lookup: dict) -> str:
    """Восстановить ссылки из меток с переводом display"""
    for key, link in links.items():
        display = link['display']
        target = link['target']
        
        # Очищаем display для поиска перевода
        clean_display = final_clean(display)
        
        # Ищем перевод
        translated_display = lookup.get(clean_display, display)
        
        # Если был префикс ..., сохраняем
        if display.startswith('...') and not translated_display.startswith('...'):
            translated_display = '...' + translated_display
        
        # Формируем ссылку всегда в формате [[перевод->оригинал]]
        if display != target:
            new_link = f'[[{translated_display}->{target}]]'
        else:
            new_link = f'[[{translated_display}->{display}]]'
        
        text = text.replace(key, new_link)
    
    return text


# ============================================================
# Экстрактор
# ============================================================

class TwineExtractor(BaseExtractor):
    """Extract translatable strings from Twine/SugarCube HTML files"""

    def run(self, source_dir: str = None, old_dir: str = None, **kwargs) -> int:
        source_dir = source_dir or self.config['paths']['new']
        old_dir = old_dir or self.config['paths'].get('old')

        html_files = self._find_html(source_dir)
        if not html_files:
            print("No HTML files found")
            return 0

        entries = []
        for fp in html_files:
            print(f"Parsing: {fp.name}")
            file_entries = self._parse_file(str(fp))
            print(f"  Found {len(file_entries)} strings")
            entries.extend(file_entries)

        print(f"Total: {len(entries)} strings")

        if old_dir and os.path.exists(old_dir):
            old_html_files = self._find_html(old_dir)
            if old_html_files:
                merged = self._merge(entries, str(old_html_files[0]))
                print(f"Merged: {merged} existing translations")

        seen = set()
        normalized = []
        for e in entries:
            key = f"{e['original']}|{e['context']}"
            if key not in seen:
                seen.add(key)
                normalized.append(self._entry(
                    original=e['original'],
                    translation=e.get('translation'),
                    context=e.get('context', '')
                ))

        count = self.db.insert_batch(normalized)
        print(f"Stored: {count} entries")
        s = self.db.stats()
        print(f"Total: {s['total']}, Translated: {s['translated']}")
        return count

    def _find_html(self, path: str) -> list:
        p = Path(path)
        if p.is_file() and p.suffix == '.html':
            return [p]
        elif p.is_dir():
            return list(p.rglob('*.html'))
        return []

    def _parse_file(self, path: str) -> list:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        entries = []
        pattern = re.compile(r'<tw-passagedata(\s[^>]*?)>(.*?)</tw-passagedata>', re.DOTALL)

        for match in pattern.finditer(content):
            attrs = match.group(1)
            inner = match.group(2)

            name_match = re.search(r'name="([^"]*)"', attrs)
            name = name_match.group(1) if name_match else ''

            if name in SKIP_PASSAGES:
                continue
            if not inner.strip():
                continue

            # Общая обработка текста
            decoded = process_passage_text(inner)

            # Разбиваем на строки
            lines = [l.strip() for l in decoded.split('\n') if l.strip()]
            
            for line in lines:
                if skip_line(line):
                    continue

                parts = extract_parts(line)

                for part in parts:
                    final = final_clean(part)
                    if final:
                        entries.append(self._entry(
                            original=final,
                            translation='',
                            context=name
                        ))

        return entries

    def _merge(self, entries: list, old_path: str) -> int:
        if not os.path.exists(old_path):
            return 0
        old_entries = self._parse_file(old_path)
        old_map = {}
        for e in old_entries:
            key = f"{e['original']}|{e['context']}"
            if e.get('translation') and e['translation'] != e['original']:
                old_map[key] = e['translation']
        merged = 0
        for e in entries:
            key = f"{e['original']}|{e['context']}"
            if not e.get('translation') and key in old_map:
                e['translation'] = old_map[key]
                merged += 1
        return merged