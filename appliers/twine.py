"""
Twine/SugarCube HTML applier
"""

import os
import re
import html as html_module
from pathlib import Path
from appliers.base import BaseApplier
from extractors.twine import (
    process_passage_text,
    skip_line,
    extract_parts,
    final_clean,
    SKIP_PASSAGES
)


class TwineApplier(BaseApplier):
    """Apply translations to Twine/SugarCube HTML"""
    
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
            
            if name in SKIP_PASSAGES:
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
        """Перевести содержимое пассажа, работая как экстрактор"""
        
        # Шаг 1: unescape HTML entities (КАК В ЭКСТРАКТОРЕ)
        decoded = html_module.unescape(inner)
        decoded = html_module.unescape(decoded)
        
        # Шаг 2: удаляем макросы <<...>> (КАК В ЭКСТРАКТОРЕ)
        # Но сохраняем их для восстановления
        macros = {}
        macro_counter = 0
        
        def save_macro(match):
            nonlocal macro_counter
            key = f'__MACRO_{macro_counter}__'
            macros[key] = match.group(0)
            macro_counter += 1
            return key
        
        decoded = re.sub(r'<<.*?>>', save_macro, decoded)
        
        # Шаг 3: удаляем HTML теги (КАК В ЭКСТРАКТОРЕ)
        # Но сохраняем их для восстановления
        tags = {}
        tag_counter = 0
        
        def save_tag(match):
            nonlocal tag_counter
            key = f'__TAG_{tag_counter}__'
            tags[key] = match.group(0)
            tag_counter += 1
            return f'\n{key}\n'
        
        decoded = re.sub(r'<[^>]+>', save_tag, decoded)
        
        # Шаг 3.5: сохраняем ссылки [[...]]
        links = {}
        link_counter = 0
        
        def save_link(match):
            nonlocal link_counter
            inner_text = match.group(1).strip()
            
            # Разбираем ссылку
            if '->' in inner_text:
                display, target = inner_text.split('->', 1)
                display = display.strip()
                target = target.strip()
            elif '|' in inner_text:
                display, target = inner_text.split('|', 1)
                display = display.strip()
                target = target.strip()
            else:
                # Простая ссылка [[text]] — target = text
                display = inner_text
                target = inner_text
            
            key = f'__LINK_{link_counter}__'
            links[key] = {
                'display': display,
                'target': target
            }
            link_counter += 1
            return key
        
        decoded = re.sub(r'\[\[(.+?)\]\]', save_link, decoded)
        
        # Шаг 4: разбиваем на строки и обрабатываем КАК В ЭКСТРАКТОРЕ
        lines = decoded.split('\n')
        result_lines = []
        
        for line in lines:
            stripped = line.strip()
            
            # Если это сохранённый тег — восстанавливаем как есть
            if stripped in tags:
                result_lines.append(tags[stripped])
                continue
            
            # Пропускаем мусор
            if skip_line(stripped):
                result_lines.append(line)
                continue
            
            # Извлекаем части (текст и ссылки)
            # Теперь ссылки уже заменены на __LINK_X__, так что extract_parts 
            # вернёт только чистый текст
            parts = extract_parts(stripped)
            
            # Переводим каждую часть
            translated_parts = []
            for part in parts:
                cleaned = final_clean(part)
                
                if cleaned in lookup:
                    # Нашли перевод — заменяем
                    translated = lookup[cleaned]
                    # Если в оригинале было оформление (кавычки, ...), сохраняем
                    if part != cleaned:
                        # Находим, чем отличается оригинал от очищенного
                        prefix = ''
                        suffix = ''
                        if part.startswith('...') and not cleaned.startswith('...'):
                            prefix = '...'
                        # Простые кавычки
                        if part.startswith('"') and part.endswith('"'):
                            translated = f'{prefix}"{translated}"'
                        elif part.startswith("'") and part.endswith("'"):
                            translated = f"{prefix}'{translated}'"
                        else:
                            translated = prefix + translated
                    translated_parts.append(translated)
                else:
                    # Нет перевода — оставляем как есть
                    translated_parts.append(part)
            
            # Собираем строку обратно
            result_line = stripped
            for original_part, translated_part in zip(parts, translated_parts):
                result_line = result_line.replace(original_part, translated_part, 1)
            
            result_lines.append(result_line)
        
        # Собираем текст обратно
        decoded = '\n'.join(result_lines)
        
        # Шаг 5: восстанавливаем ссылки с переводом
        for key, link in links.items():
            display = link['display']
            target = link['target']
            
            # Очищаем display для поиска перевода
            clean_display = final_clean(display)
            
            # Ищем перевод для display
            translated_display = lookup.get(clean_display, display)
            
            # Сохраняем префикс ... если был
            if display.startswith('...') and not translated_display.startswith('...'):
                translated_display = '...' + translated_display
            
            # Всегда создаём ссылку в формате [[перевод->оригинал]]
            new_link = f'[[{translated_display}->{target}]]'
            
            decoded = decoded.replace(key, new_link)
        
        # Восстанавливаем макросы
        for key, original in macros.items():
            decoded = decoded.replace(key, original)
        
        # Escape обратно в HTML entities
        encoded = html_module.escape(decoded, quote=False)
        
        return encoded