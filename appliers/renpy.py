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
        
        # Build lookup
        lookup = {}
        for t in translations:
            if t['original'] not in lookup:
                lookup[t['original']] = t['translation']
        
        print(f"Translations: {len(lookup)}")
        
        # Generate files
        generated = 0
        for fp in self._find_templates(source_dir):
            rel = os.path.relpath(fp, source_dir)
            out = os.path.join(target_dir, rel)
            if not out.endswith('.rpy'):
                out += '.rpy'
            
            os.makedirs(os.path.dirname(out), exist_ok=True)
            self._generate(fp, out, lookup)
            generated += 1
        
        print(f"Files: {generated}")
        return generated
    
    def _find_templates(self, directory: str) -> list:
        """Find template files"""
        import pathlib
        return [str(p) for p in pathlib.Path(directory).rglob('*.rpy')]
    
    def _generate(self, template: str, output: str, lookup: dict):
        """Generate translated file from template"""
        with open(template, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        with open(output, 'w', encoding='utf-8') as out:
            i = 0
            while i < len(lines):
                line = lines[i]
                
                # old/new
                if 'old "' in line and i + 1 < len(lines):
                    nxt = lines[i + 1]
                    if 'new "' in nxt:
                        orig = self._q(line)
                        if orig:
                            clean = orig.replace('\\"', '"')
                            trans = lookup.get(clean, orig)
                            out.write(f'    old "{orig}"\n')
                            out.write(f'    new "{trans.replace(chr(34), chr(92)+chr(34))}"\n')
                            i += 2
                            continue
                
                # # name "text" / name "text"
                if line.strip().startswith('# ') and '"' in line and i + 1 < len(lines):
                    name = line.strip()[2:].split('"')[0].strip()
                    nxt = lines[i + 1]
                    if name and name in nxt:
                        orig = self._q(line)
                        if orig:
                            clean = orig.replace('\\"', '"')
                            trans = lookup.get(clean, orig)
                            out.write(f'    # {name} "{orig}"\n')
                            out.write(f'    {name} "{trans.replace(chr(34), chr(92)+chr(34))}"\n')
                            i += 2
                            continue
                
                out.write(line)
                i += 1
    
    @staticmethod
    def _q(line: str) -> str:
        start = line.find('"')
        end = line.rfind('"')
        return line[start + 1:end] if start >= 0 and end > start else ''