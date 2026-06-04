"""
Ren'Py TL applier
"""

import os
from collections import defaultdict
from appliers.base import BaseApplier


class RenpyApplier(BaseApplier):
    """Apply translations to Ren'Py .rpy files"""
    
    def run(self, target_dir: str = None, **kwargs) -> int:
        target_dir = target_dir or self.config['paths']['output']
        source_dir = self.config['paths']['new']
        
        print(f"Templates: {source_dir}")
        print(f"Output: {target_dir}")
        
        translations = self.db.get_translated(self.source_lang, self.target_lang)
        
        # Строим lookup: (original, context) -> translation
        # Плюс fallback: original -> translation (для строк без контекста)
        lookup = {}
        fallback = {}
        
        for t in translations:
            key = (t['original'], t.get('context', ''))
            if key not in lookup:
                lookup[key] = t['translation']
            if t['original'] not in fallback:
                fallback[t['original']] = t['translation']
        
        print(f"Translations: {len(lookup)} unique, {len(fallback)} originals")
        
        generated = 0
        for fp in self._find_templates(source_dir):
            rel = os.path.relpath(fp, source_dir)
            out = os.path.join(target_dir, rel)
            if not out.endswith('.rpy'):
                out += '.rpy'
            
            os.makedirs(os.path.dirname(out), exist_ok=True)
            self._generate(fp, out, lookup, fallback)
            generated += 1
        
        print(f"Files: {generated}")
        return generated
    
    def _find_templates(self, directory: str) -> list:
        """Find template files"""
        import pathlib
        return [str(p) for p in pathlib.Path(directory).rglob('*.rpy')]
    
    def _generate(self, template: str, output: str, lookup: dict, fallback: dict):
        """Generate translated file from template"""
        with open(template, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        with open(output, 'w', encoding='utf-8') as out:
            i = 0
            while i < len(lines):
                line = lines[i]
                stripped = line.strip()
                
                # === ПАТТЕРН 1: old/new (без контекста) ===
                if 'old "' in line and i + 1 < len(lines):
                    nxt = lines[i + 1]
                    if 'new "' in nxt:
                        orig = self._q(line)
                        if orig:
                            clean = orig.replace('\\"', '"')
                            trans = fallback.get(clean, clean)
                            out.write(f'    old "{orig}"\n')
                            out.write(f'    new "{self._escape(trans)}"\n')
                            i += 2
                            continue
                
                # === ПАТТЕРН 2: # name "text" / name "text" ===
                if (stripped.startswith('# ') and '"' in stripped and 
                    not stripped.startswith('# "') and i + 1 < len(lines)):
                    
                    name = stripped[2:].split('"')[0].strip()
                    orig = self._q(line)
                    
                    if name and orig:
                        clean = orig.replace('\\"', '"')
                        # Ищем с контекстом, fallback без контекста
                        trans = lookup.get((clean, name), fallback.get(clean, clean))
                        
                        nxt1 = lines[i + 1].strip()
                        
                        if nxt1.startswith(name + ' "'):
                            out.write(f'    # {name} "{orig}"\n')
                            out.write(f'    {name} "{self._escape(trans)}"\n')
                            i += 2
                            continue
                        
                        if i + 2 < len(lines):
                            nxt2 = lines[i + 2].strip()
                            if nxt2.startswith(name + ' "'):
                                out.write(f'    # {name} "{orig}"\n')
                                out.write(lines[i + 1])
                                out.write(f'    {name} "{self._escape(trans)}"\n')
                                i += 3
                                continue
                
                # === ПАТТЕРН 3: # "text" / "text" (без контекста) ===
                if stripped.startswith('# "') and i + 1 < len(lines):
                    orig = self._q(line)
                    
                    if orig:
                        clean = orig.replace('\\"', '"')
                        trans = fallback.get(clean, clean)
                        
                        nxt1 = lines[i + 1].strip()
                        
                        if nxt1.startswith('"'):
                            out.write(f'    # "{orig}"\n')
                            out.write(f'    "{self._escape(trans)}"\n')
                            i += 2
                            continue
                        
                        if i + 2 < len(lines):
                            nxt2 = lines[i + 2].strip()
                            if nxt2.startswith('"'):
                                out.write(f'    # "{orig}"\n')
                                out.write(lines[i + 1])
                                out.write(f'    "{self._escape(trans)}"\n')
                                i += 3
                                continue
                
                out.write(line)
                i += 1

    @staticmethod
    def _escape(text: str) -> str:
        """Escape quotes for Ren'Py"""
        return text.replace('"', '\\"')

    @staticmethod
    def _q(line: str) -> str:
        start = line.find('"')
        end = line.rfind('"')
        return line[start + 1:end] if start >= 0 and end > start else ''