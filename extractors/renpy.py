"""
Ren'Py TL extractor
"""

import os
import re
from pathlib import Path
from extractors.base import BaseExtractor


class RenpyExtractor(BaseExtractor):
    """Extract strings from Ren'Py .rpy translation files"""
    
    def run(self, source_dir: str = None, old_dir: str = None, **kwargs) -> int:
        source_dir = source_dir or self.config['paths']['new']
        old_dir = old_dir or self.config['paths'].get('old')
        
        print(f"Source: {source_dir}")
        
        entries = self._parse(source_dir)
        print(f"Parsed: {len(entries)} strings")
        
        # Merge old translations
        if old_dir and os.path.exists(old_dir):
            merged = self._merge(entries, old_dir)
            print(f"Merged: {merged} existing translations")
        
        # Store
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
    
    def _parse(self, directory: str) -> list:
        """Parse all .rpy files"""
        entries = []
        seen = set()
        
        for fp in Path(directory).rglob('*.rpy'):
            for e in self._parse_file(str(fp)):
                key = f"{e['original']}|{e['context']}"
                if key not in seen:
                    seen.add(key)
                    entries.append(e)
        
        return entries
    
    def _parse_file(self, path: str) -> list:
        """Parse single file"""
        if not os.path.exists(path):
            return []
        
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        entries = []
        block = {'type': '', 'label': ''}
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            if not line:
                i += 1
                continue
            
            # Skip source ref comments
            if re.match(r'#\s*game/.+:\d+', line):
                i += 1
                continue
            
            # Block header
            if line.startswith('translate '):
                parts = line.split()
                block = {
                    'type': parts[2].rstrip(':') if len(parts) > 2 else '',
                    'label': parts[3].rstrip(':') if len(parts) > 3 else ''
                }
                i += 1
                continue
            
            if not block['type']:
                i += 1
                continue
            
            # old/new
            if line.startswith('old "') and i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if nxt.startswith('new "'):
                    old = self._q(line)
                    new = self._q(nxt)
                    if old and not self._is_vars(old):
                        entries.append({
                            'original': self._unescape(old),
                            'translation': self._unescape(new) if new else '',
                            'context': ''
                        })
                    i += 2
                    continue
            
            # # name "text" / name "text"
            if line.startswith('# ') and '"' in line and not line.startswith('# "') and i + 1 < len(lines):
                name = line[2:].split('"')[0].strip()
                if name and re.match(r'^[a-zA-Z_]\w*$', name):
                    nxt = lines[i + 1].strip()
                    if nxt.startswith(name + ' "') or nxt.startswith(name + '\t"'):
                        old = self._q(line)
                        new = self._q(nxt)
                        if old and not self._is_vars(old):
                            entries.append({
                                'original': self._unescape(old),
                                'translation': self._unescape(new) if new else '',
                                'context': name
                            })
                        i += 2
                        continue
            
            # # "text" / "text"
            if line.startswith('# "') and i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if nxt.startswith('"') and not nxt.startswith('""'):
                    old = self._q(line)
                    new = self._q(nxt)
                    if old and not self._is_vars(old):
                        entries.append({
                            'original': self._unescape(old),
                            'translation': self._unescape(new) if new else '',
                            'context': ''
                        })
                    i += 2
                    continue
            
            i += 1
        
        return entries
    
    def _merge(self, entries: list, old_dir: str) -> int:
        """Merge old translations, skip untranslated (same as original)"""
        old = self._parse(old_dir)
        
        old_map = {}
        skipped_untranslated = 0
        
        for e in old:
            key = f"{e['original']}|{e['context']}"
            translation = e.get('translation', '')
            
            # Пропускаем пустые и непереведенные (совпадает с оригиналом)
            if not translation or translation == e['original']:
                skipped_untranslated += 1
                continue
            
            if key not in old_map:
                old_map[key] = translation
        
        if skipped_untranslated:
            print(f"  Skipped {skipped_untranslated} untranslated entries from old files")
        
        merged = 0
        for e in entries:
            key = f"{e['original']}|{e['context']}"
            if not e.get('translation') and key in old_map:
                e['translation'] = old_map[key]
                merged += 1
        
        return merged
    
    @staticmethod
    def _q(line: str) -> str:
        start = line.find('"')
        end = line.rfind('"')
        return line[start + 1:end] if start >= 0 and end > start else ''
    
    @staticmethod
    def _unescape(s: str) -> str:
        return s.replace('\\"', '"').replace('\\n', '\n')
    
    @staticmethod
    def _is_vars(text: str) -> bool:
        if not text:
            return True
        cleaned = text
        cleaned = re.sub(r'\[.*?\]', '', cleaned)
        cleaned = re.sub(r'\{.*?\}', '', cleaned)
        cleaned = re.sub(r'%\(.*?\)[sd]|%[sd]', '', cleaned)
        return len(cleaned.strip()) == 0