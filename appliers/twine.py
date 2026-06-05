"""
Twine/SugarCube HTML applier
"""

import os
import re
import html as html_module
from pathlib import Path
from appliers.base import BaseApplier


class TwineApplier(BaseApplier):
    """Apply translations to Twine/SugarCube HTML"""
    
    SKIP_PASSAGES = {
        'StoryInit', 'StoryReady', 'PassageReady', 'PassageDone',
        'PassageHeader', 'PassageFooter', 'StoryMenu', 'StoryBanner',
        'StoryCaption', 'StorySubtitle', 'StoryTitle', 'StoryAuthor'
    }
    
    def run(self, target_dir: str = None, **kwargs) -> int:
        target_dir = target_dir or self.config['paths']['output']
        source_dir = self.config['paths']['new']
        
        html_files = self._find_html(source_dir)
        if not html_files:
            print("No HTML files found")
            return 0
        
        translations = self.db.get_translated(self.source_lang, self.target_lang)
        lookup = {}
        for t in translations:
            if t['original'] not in lookup:
                lookup[t['original']] = t['translation']
        
        print(f"Translations: {len(lookup)}")
        
        if len(html_files) == 1 and not os.path.isdir(target_dir):
            os.makedirs(os.path.dirname(target_dir) or '.', exist_ok=True)
            self._generate(str(html_files[0]), target_dir, lookup)
            generated = 1
        else:
            os.makedirs(target_dir, exist_ok=True)
            generated = 0
            for fp in html_files:
                out = os.path.join(target_dir, fp.name)
                self._generate(str(fp), out, lookup)
                generated += 1
        
        print(f"Files: {generated}")
        return generated
    
    def _find_html(self, path: str) -> list:
        p = Path(path)
        if p.is_file() and p.suffix == '.html':
            return [p]
        elif p.is_dir():
            return list(p.rglob('*.html'))
        return []
    
    def _generate(self, template: str, output: str, lookup: dict):
        with open(template, 'r', encoding='utf-8') as f:
            content = f.read()
        
        def process_passage(match):
            attrs = match.group(1)
            inner = match.group(2)
            
            name_match = re.search(r'name="([^"]*)"', attrs)
            name = name_match.group(1) if name_match else ''
            
            if name in self.SKIP_PASSAGES:
                return match.group(0)
            
            translated = self._translate_passage(inner, lookup)
            return f'<tw-passagedata{attrs}>{translated}</tw-passagedata>'
        
        result = re.sub(
            r'<tw-passagedata(\s[^>]*?)>(.*?)</tw-passagedata>',
            process_passage,
            content,
            flags=re.DOTALL
        )
        
        with open(output, 'w', encoding='utf-8') as f:
            f.write(result)
    
    def _translate_passage(self, inner: str, lookup: dict) -> str:
        """Перевести содержимое пассажа"""
        
        # Шаг 1: unescape HTML entities
        decoded = html_module.unescape(inner)
        
        # Шаг 2: маскируем макросы <<...>> (ДО ВСЕГО)
        decoded, macros = self._mask_macros(decoded)
        
        # Шаг 3: маскируем ссылки [[...]]
        decoded, links = self._mask_links(decoded)
        
        # Шаг 4: переводим текст прямым replace
        for original, translation in lookup.items():
            if original in decoded:
                decoded = decoded.replace(original, translation)
        
        # Шаг 5: восстанавливаем макросы (оригиналы)
        for key, original in macros.items():
            decoded = decoded.replace(key, original)
        
        # Шаг 6: восстанавливаем ссылки (с переводом display)
        for key, link in links.items():
            display = link['display']
            target = link['target']
            translated_display = lookup.get(display, display)
            new_link = f'[[{translated_display}->{target}]]'
            decoded = decoded.replace(key, new_link)
        
        # Шаг 7: escape обратно (но не трогаем кавычки)
        encoded = html_module.escape(decoded, quote=False)
        
        return encoded
    
    def _mask_macros(self, text: str) -> tuple:
        """Заменить макросы <<...>> на метки"""
        macros = {}
        counter = 0
        
        def replace_macro(match):
            nonlocal counter
            key = f'__MACRO_{counter}__'
            macros[key] = match.group(0)
            counter += 1
            return key
        
        # Используем .*? чтобы ловить макросы с > внутри
        result = re.sub(r'<<.*?>>', replace_macro, text)
        return result, macros
    
    def _mask_links(self, text: str) -> tuple:
        """Заменить ссылки [[...]] на метки"""
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
            links[key] = {'display': display, 'target': target}
            counter += 1
            return key
        
        result = re.sub(r'\[\[(.+?)\]\]', replace_link, text)
        return result, links