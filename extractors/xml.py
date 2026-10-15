"""
XML extractor

Общие помощники (compute_context, walk_text_nodes, is_vars) живут здесь
и импортируются аппликатором — чтобы обход дерева и правила контекста
были гарантированно идентичны.
"""

import os
import re
from pathlib import Path
import xml.etree.ElementTree as ET
from extractors.base import BaseExtractor


# Имена тегов и атрибутов, которые несут метаданные.
# Они (а) служат подсказкой-контекстом для ИИ, (б) сами контекста не имеют.
CTX_ATTRS = ('label', 'name', 'id', 'key', 'title')
CTX_TAGS = ('label', 'name', 'id', 'key', 'title')


# --------------------------------------------------------
# Общие помощники (используются и экстрактором, и аппликатором)
# --------------------------------------------------------

def compute_context(element, parent) -> str:
    """
    Короткая подсказка для ИИ-переводчика. Пустая строка — если подсказки нет.

    Приоритет:
      0. Элемент сам является метаданными → контекста нет
         (иначе <House.label>Old Manor</House.label> сам себе подсказка).
      1. Свой атрибут-метаданное     (<line label="Alice">…)
      2. Сосед с совпадающим dotted-префиксом
         (<House.label> рядом с <House.description>).
      3. Сосед с зарезервированным именем (<key> рядом с <value>).
      4. Атрибут-метаданное у родителя.
    """
    # 0. Сам носитель метаданных — без контекста.
    own_suffix = element.tag.rsplit('.', 1)[-1]
    if own_suffix in CTX_TAGS:
        return ''

    # 1. Свой атрибут.
    for attr in CTX_ATTRS:
        val = element.attrib.get(attr)
        if val and val.strip():
            return val.strip()

    if parent is None:
        return ''

    # 2. Сосед с тем же dotted-префиксом: <House.label> / <House.description>.
    tag = element.tag
    if '.' in tag:
        prefix = tag.rsplit('.', 1)[0]
        for sibling in parent:
            if sibling is element:
                continue
            st = sibling.tag
            if st.startswith(prefix + '.'):
                suffix = st[len(prefix) + 1:]
                if suffix in CTX_TAGS and sibling.text and sibling.text.strip():
                    return sibling.text.strip()

    # 3. Сосед с зарезервированным именем: <key> / <value>.
    for sibling in parent:
        if sibling is element:
            continue
        if sibling.tag in CTX_TAGS and sibling.text and sibling.text.strip():
            return sibling.text.strip()

    # 4. Атрибут родителя.
    for attr in CTX_ATTRS:
        val = parent.attrib.get(attr)
        if val and val.strip():
            return val.strip()

    return ''


def walk_text_nodes(root):
    """
    Обойти дерево в порядке документа.

    Yield: (element, attr_name, raw_text, context)
      attr_name — 'text' или 'tail'; указывает, какое поле мутировать/читать.
      raw_text  — исходная строка (с ведущими/хвостовыми пробелами).
      context   — подсказка из compute_context.

    Для tail-строк контекст берётся у родителя — tail семантически
    принадлежит тексту родителя, а не элементу, после которого стоит.
    """
    yield from _walk_node(root, parent=None)


def _walk_node(element, parent):
    context = compute_context(element, parent)

    if element.text and element.text.strip():
        yield (element, 'text', element.text, context)

    for child in element:
        yield from _walk_node(child, element)
        if child.tail and child.tail.strip():
            yield (child, 'tail', child.tail, context)


def is_vars(text: str) -> bool:
    """True, если строка состоит только из плейсхолдеров / переменных."""
    if not text:
        return True
    cleaned = re.sub(r'\[.*?\]', '', text)
    cleaned = re.sub(r'\{.*?\}', '', cleaned)
    cleaned = re.sub(r'%\(.*?\)[sd]|%[sd]', '', cleaned)
    return len(cleaned.strip()) == 0


# --------------------------------------------------------
# Экстрактор
# --------------------------------------------------------

class XmlExtractor(BaseExtractor):
    """Extract text nodes from XML files"""

    def run(self, source_dir: str = None, old_dir: str = None, **kwargs) -> int:
        source_dir = source_dir or self.config['paths']['new']
        old_dir = old_dir or self.config['paths'].get('old')

        print(f"Source: {source_dir}")

        entries = self._parse(source_dir)
        print(f"Parsed: {len(entries)} strings")

        if old_dir and os.path.exists(old_dir):
            merged = self._merge(entries, old_dir)
            print(f"Merged: {merged} existing translations")

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
        print(f"Stored: {count} entries")

        s = self.db.stats()
        print(f"Total: {s['total']}, Translated: {s['translated']}")

        return count

    def _parse(self, directory: str) -> list:
        entries = []
        seen = set()
        for fp in Path(directory).rglob('*.xml'):
            for e in self._parse_file(str(fp)):
                key = f"{e['original']}|{e['context']}"
                if key not in seen:
                    seen.add(key)
                    entries.append(e)
        return entries

    def _parse_file(self, path: str) -> list:
        if not os.path.exists(path):
            return []
        try:
            tree = ET.parse(path)
        except ET.ParseError as e:
            print(f"  Warning: failed to parse {path}: {e}")
            return []

        entries = []
        for _element, _attr, raw, context in walk_text_nodes(tree.getroot()):
            clean = raw.strip()
            if is_vars(clean):
                continue
            entries.append({
                'original': clean,
                'translation': '',
                'context': context,
            })
        return entries

    def _merge(self, entries: list, old_dir: str) -> int:
        old_entries = self._parse(old_dir)

        old_map = {}
        skipped = 0
        for e in old_entries:
            key = f"{e['original']}|{e['context']}"
            translation = e.get('translation', '')
            # Пропускаем пустые и «непереведённые» (совпадают с оригиналом)
            if not translation or translation == e['original']:
                skipped += 1
                continue
            if key not in old_map:
                old_map[key] = translation

        if skipped:
            print(f"  Skipped {skipped} untranslated entries from old files")

        merged = 0
        for e in entries:
            key = f"{e['original']}|{e['context']}"
            if not e.get('translation') and key in old_map:
                e['translation'] = old_map[key]
                merged += 1
        return merged