"""
TyranoScript .ks applier

Симметричен экстрактору:
- Использует тот же tokenize_ks, is_skippable_line, final_clean_tyrano,
  compute_context, update_character_from_tag.
- Заменяет только содержимое text-токенов, теги не трогает.
- Блоки [iscript]/[html] маскируются и восстанавливаются без изменений.
- Lookup: сначала точное (original, context), затем fallback по original.
- СОХРАНЯЕТ структуру подпапок относительно source_dir.
"""

import os
import re
from pathlib import Path
from appliers.base import BaseApplier
from extractors.tyrano import (
    tokenize_ks,
    is_skippable_line,
    final_clean_tyrano,
    compute_context,
    update_character_from_tag,
    RE_ISCRIPT_BLOCK,
    RE_HTML_BLOCK,
    BLOCK_KEY_TPL,
)


# Папки, которые не считаем частью структуры (мусор)
SKIP_DIRS = {'__MACOSX', '.git', '.svn', '.idea', '.vscode', 'node_modules'}


class TyranoApplier(BaseApplier):
    """Apply translations to TyranoScript .ks files"""

    def run(self, target_dir: str = None, **kwargs) -> int:
        target_dir = target_dir or self.config['paths']['output']
        source_dir = self.config['paths']['new']

        ks_files = self._find_ks(source_dir)
        if not ks_files:
            print('No .ks files found')
            return 0

        # Определяем корень источника для расчёта относительных путей
        src_path = Path(source_dir)
        if src_path.is_file():
            source_root = src_path.parent
        else:
            source_root = src_path

        translations = self.db.get_translated(self.source_lang, self.target_lang)

        # Двухуровневый lookup: точный по контексту и fallback по оригиналу
        lookup_ctx = {}
        lookup_any = {}
        for t in translations:
            lookup_ctx[(t['original'], t['context'])] = t['translation']
            if t['original'] not in lookup_any:
                lookup_any[t['original']] = t['translation']

        print(f'Translations: {len(lookup_any)}')

        os.makedirs(target_dir, exist_ok=True)
        generated = 0
        for fp in ks_files:
            # Сохраняем относительный путь от корня источника
            try:
                rel = fp.relative_to(source_root)
            except ValueError:
                # На случай, если fp вне source_root (симлинк, ..)
                rel = Path(fp.name)

            out = Path(target_dir) / rel
            out.parent.mkdir(parents=True, exist_ok=True)

            self._generate(str(fp), str(out), fp.stem, lookup_ctx, lookup_any)
            print(f'  {rel} -> {out}')
            generated += 1

        print(f'Files: {generated}')
        return generated

    # --------------------------------------------------------
    # Внутреннее
    # --------------------------------------------------------

    def _find_ks(self, path: str) -> list:
        p = Path(path)
        if p.is_file() and p.suffix == '.ks':
            return [p]
        if p.is_dir():
            return sorted(
                f for f in p.rglob('*.ks')
                if not any(part in SKIP_DIRS for part in f.parts)
            )
        return []

    def _generate(self, template: str, output: str, filename: str,
                  lookup_ctx: dict, lookup_any: dict) -> None:
        with open(template, 'r', encoding='utf-8') as f:
            content = f.read()

        # Определяем стиль переводов строк и работаем с LF
        newline = '\r\n' if '\r\n' in content else '\n'
        if newline == '\r\n':
            content = content.replace('\r\n', '\n')

        # 1) Маскируем блоки кода, чтобы они не попали в разбор
        blocks = {}
        counter = [0]

        def mask_block(m):
            key = BLOCK_KEY_TPL.format(counter[0])
            blocks[key] = m.group(0)
            counter[0] += 1
            return key

        content = RE_ISCRIPT_BLOCK.sub(mask_block, content)
        content = RE_HTML_BLOCK.sub(mask_block, content)

        # 2) Токенизация и построчная замена
        tokens = tokenize_ks(content)
        out_parts = []
        current_char = ''

        for tok in tokens:
            if tok['type'] == 'tag':
                out_parts.append(tok['content'])
                current_char = update_character_from_tag(tok['content'], current_char)
                continue

            lines = tok['content'].split('\n')
            new_lines = []
            for line in lines:
                stripped = line.strip()

                if not stripped:
                    new_lines.append(line)
                    continue

                # '#Name' — конвенция имени персонажа
                if stripped.startswith('#'):
                    name = stripped[1:].strip()
                    if name:
                        current_char = name
                    new_lines.append(line)
                    continue

                if is_skippable_line(stripped):
                    new_lines.append(line)
                    continue

                new_lines.append(self._replace_line(
                    line, filename, current_char, lookup_ctx, lookup_any,
                ))
            out_parts.append('\n'.join(new_lines))

        content = ''.join(out_parts)

        # 3) Восстанавливаем блоки кода
        for key, original in blocks.items():
            content = content.replace(key, original)

        with open(output, 'w', encoding='utf-8') as f:
            f.write(content.replace('\n', newline))

    def _replace_line(self, line: str, filename: str, char_name: str,
                      lookup_ctx: dict, lookup_any: dict) -> str:
        """
        Заменить текстовые сегменты в одной строке, теги не трогать.
        Сохраняет обрамляющие пробелы (leading/trailing).
        """
        segs = tokenize_ks(line)
        context = compute_context(filename, char_name)
        out = []

        for s in segs:
            if s['type'] == 'tag':
                out.append(s['content'])
                continue

            text = s['content']
            cleaned = final_clean_tyrano(text)
            if not cleaned:
                out.append(text)
                continue

            # 1) точный lookup (original, context)
            tr = lookup_ctx.get((cleaned, context))
            # 2) fallback — (original) без контекста
            if tr is None:
                tr = lookup_any.get(cleaned)
            if tr is None:
                out.append(text)
                continue

            leading = re.match(r'^(\s*)', text).group(1)
            trailing = re.search(r'(\s*)$', text).group(1)
            out.append(leading + tr + trailing)

        return ''.join(out)