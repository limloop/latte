"""
Google Translate translator (free, with batch support)
"""

import time
import random
import re
from typing import Dict, Tuple, List
from tqdm import tqdm

from translators.base import BaseTranslator

try:
    from deep_translator import GoogleTranslator
    GOOGLE_OK = True
except ImportError:
    GOOGLE_OK = False


class GoogleTranslator_(BaseTranslator):
    """Google Translate based translator"""
    
    def __init__(self, config: dict, db):
        super().__init__(config, db)
        
        if not GOOGLE_OK:
            raise ImportError("pip install deep-translator")
        
        self.batch_size = self.cfg.get('batch', 50)
        self.delay_min = self.cfg.get('delay_min', 1.0)
        self.delay_max = self.cfg.get('delay_max', 2.0)
        self._warned = False
    
    def run(self, dry_run: bool = False, **kwargs) -> dict:
        print(f"\n{self.source_lang} → {self.target_lang}")
        print(f"Engine: Google Translate, Batch: {self.batch_size}")
        print("⚠ Google Translate will break Ren'Py variables!")
        print("  Strings with [var], {var}, %(var)s will be skipped.")
        
        s = self.db.stats()
        print(f"Untranslated: {s['untranslated']}")
        
        if s['untranslated'] == 0:
            print("Nothing to translate!")
            return s
        
        if dry_run:
            for i, e in enumerate(self.db.get_untranslated(self.source_lang, self.target_lang, 10), 1):
                ctx = f" [{e['context']}]" if e.get('context') else ""
                print(f"  {i}.{ctx} {e['original'][:80]}")
            return s
        
        total = 0
        
        while True:
            untranslated = self.db.get_untranslated(self.source_lang, self.target_lang)
            if not untranslated:
                print("\n✓ Done!")
                break
            
            to_translate = []
            skipped = 0
            for e in untranslated:
                if self._has_variables(e['original']):
                    skipped += 1
                else:
                    to_translate.append(e)
            
            if skipped and not self._warned:
                print(f"  Skipped {skipped} strings with variables (can't translate with Google)")
                self._warned = True
            
            if not to_translate:
                print("  All remaining strings contain variables. Nothing to translate.")
                break
            
            print(f"\nRemaining: {len(to_translate)} (+ {skipped} with variables)")
            
            batches = [to_translate[i:i + self.batch_size]
                      for i in range(0, len(to_translate), self.batch_size)]
            
            done = 0
            
            with tqdm(total=len(to_translate), desc="Translating") as pbar:
                for batch in batches:
                    try:
                        updates = self._translate_batch(batch)
                        if updates:
                            self.db.update_batch(updates)
                            done += len(updates)
                            total += len(updates)
                        pbar.set_postfix({'done': total})
                    except Exception as e:
                        print(f"\nError: {e}")
                    
                    pbar.update(len(batch))
            
            print(f"  Done: {done}")
            if done == 0:
                break
        
        return self.db.stats()
    
    def _translate_batch(self, entries: List[Dict]) -> List[Tuple[int, str]]:
        """Translate batch of entries"""
        try:
            time.sleep(random.uniform(self.delay_min, self.delay_max))
            
            translator = GoogleTranslator(
                source=self.source_lang,
                target=self.target_lang
            )
            
            texts = [e['original'] for e in entries]
            translated = translator.translate_batch(texts)
            
            updates = []
            for entry, trans in zip(entries, translated):
                if trans and self._valid(entry['original'], str(trans)):
                    updates.append((entry['id'], str(trans)))
            
            return updates
            
        except Exception as e:
            print(f"Batch error: {e}")
            return []
    
    @staticmethod
    def _has_variables(text: str) -> bool:
        """Check if text contains Ren'Py variables"""
        return bool(re.search(r'\[.*?\]|\{.*?\}|%\(.*?\)[sd]', text))
    
    @staticmethod
    def _valid(original: str, translation: str) -> bool:
        """Validate translation"""
        if not translation or not translation.strip():
            return False
        
        if original.strip().lower() == translation.strip().lower():
            if not re.match(r'^[\d\s\.\,\!\?\-]+$', original):
                return False
        
        return True