"""
XML applier

Симметричен экстрактору:
- Использует тот же walk_text_nodes, compute_context, is_vars.
- Заменяет только text/tail-узлы, атрибуты и структуру не трогает.
- Lookup: сначала точное (original, context), затем fallback по original.
- Сохраняет структуру подпапок относительно source_dir.
"""

import os
from pathlib import Path
import xml.etree.ElementTree as ET
from appliers.base import BaseApplier
from extractors.xml import walk_text_nodes, is_vars


SKIP_DIRS = {'__MACOSX', '.git', '.svn', '.idea', '.vscode', 'node_modules'}


class XmlApplier(BaseApplier):
    """Apply translations to XML templates"""

    def run(self, target_dir: str = None, **kwargs) -> int:
        target_dir = target_dir or self.config['paths']['output']
        source_dir = self.config['paths']['new']

        templates = self._find_templates(source_dir)
        if not templates:
            print('No .xml files found')
            return 0

        # Корень источника — для расчёта относительных путей
        src_path = Path(source_dir)
        source_root = src_path.parent if src_path.is_file() else src_path

        translations = self.db.get_translated(self.source_lang, self.target_lang)

        # Двухуровневый lookup: точный по контексту + fallback по оригиналу
        lookup_ctx = {}
        lookup_any = {}
        for t in translations:
            lookup_ctx[(t['original'], t.get('context', ''))] = t['translation']
            if t['original'] not in lookup_any:
                lookup_any[t['original']] = t['translation']

        print(f"Templates: {source_dir}")
        print(f"Output: {target_dir}")
        print(f"Translations: {len(lookup_any)}")

        os.makedirs(target_dir, exist_ok=True)
        generated = 0
        for fp in templates:
            try:
                rel = fp.relative_to(source_root)
            except ValueError:
                rel = Path(fp.name)

            out = Path(target_dir) / rel
            out.parent.mkdir(parents=True, exist_ok=True)

            if self._generate(str(fp), str(out), lookup_ctx, lookup_any):
                print(f"  {rel} -> {out}")
                generated += 1

        print(f"Files: {generated}")
        return generated

    # --------------------------------------------------------
    # Внутреннее
    # --------------------------------------------------------

    def _find_templates(self, path: str) -> list:
        p = Path(path)
        if p.is_file() and p.suffix == '.xml':
            return [p]
        if p.is_dir():
            return sorted(
                f for f in p.rglob('*.xml')
                if not any(part in SKIP_DIRS for part in f.parts)
            )
        return []

    def _generate(self, template: str, output: str,
                  lookup_ctx: dict, lookup_any: dict) -> bool:
        try:
            tree = ET.parse(template)
        except ET.ParseError as e:
            print(f"  Warning: failed to parse {template}: {e}")
            return False

        for element, attr_name, raw, context in walk_text_nodes(tree.getroot()):
            clean = raw.strip()
            if is_vars(clean):
                continue

            # 1) точный lookup (original, context)
            tr = lookup_ctx.get((clean, context))
            # 2) fallback — по одному original
            if tr is None:
                tr = lookup_any.get(clean)
            if tr is None or tr == clean:
                continue

            # Сохраняем ведущие / хвостовые пробелы исходной строки
            leading = raw[:len(raw) - len(raw.lstrip())]
            trailing = raw[len(raw.rstrip()):]
            new_value = leading + tr + trailing

            if attr_name == 'text':
                element.text = new_value
            else:
                element.tail = new_value

        try:
            tree.write(output, encoding='utf-8', xml_declaration=True)
        except Exception as e:
            print(f"  Error writing {output}: {e}")
            return False
        return True