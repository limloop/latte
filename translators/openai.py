"""
OpenAI translator
"""

import json
import time
import random
import re
import threading
from typing import List, Dict, Tuple
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

from translators.base import BaseTranslator

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


class OpenaiTranslator(BaseTranslator):
    """OpenAI-based translator"""
    
    def __init__(self, config: dict, db):
        super().__init__(config, db)
        
        if OpenAI is None:
            raise ImportError("pip install openai")
        
        self.client = OpenAI(
            api_key=self.cfg['api_key'],
            base_url=self.cfg.get('base_url') or None
        )
        
        self.model = self.cfg.get('model', 'gpt-4-turbo-preview')
        self.temperature = self.cfg.get('temperature', 0.3)
        self.max_tokens = self.cfg.get('max_tokens', 4000)
        self.batch_size = self.cfg.get('batch', 10)
        self.workers = self.cfg.get('workers', 4)
        self.retries = self.cfg.get('retries', 3)
        self.delay_min = self.cfg.get('delay_min', 0.1)
        self.delay_max = self.cfg.get('delay_max', 0.5)
        self.reasoning = self.cfg.get('reasoning', False)
        
        self._lock = threading.Lock()
    
    def run(self, dry_run: bool = False, **kwargs) -> dict:
        print(f"\n{self.source_lang} → {self.target_lang}")
        print(f"Model: {self.model}, Workers: {self.workers}, Batch: {self.batch_size}")
        
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
        round_num = 0
        
        while round_num < 10:
            round_num += 1
            
            untranslated = self.db.get_untranslated(self.source_lang, self.target_lang)
            if not untranslated:
                print("\n✓ Done!")
                break
            
            print(f"\nRound {round_num}: {len(untranslated)} remaining")
            
            batches = [untranslated[i:i + self.batch_size]
                      for i in range(0, len(untranslated), self.batch_size)]
            
            done = 0
            
            with ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = {
                    executor.submit(self._batch, b): i
                    for i, b in enumerate(batches)
                }
                
                with tqdm(total=len(batches), desc="Translating") as pbar:
                    for future in as_completed(futures):
                        try:
                            updates, _ = future.result()
                            if updates:
                                self.db.update_batch(updates)
                            done += len(updates)
                            total += len(updates)
                            pbar.set_postfix({'done': total})
                        except Exception as e:
                            print(f"\nError: {e}")
                        pbar.update(1)
            
            print(f"  Done: {done}")
            if done == 0:
                break
        
        return self.db.stats()
    
    def _batch(self, batch: List[Dict]) -> Tuple[List, int]:
        """Process one batch"""
        try:
            items = [{
                'id': i,
                'context': e.get('context', ''),
                'original': e['original']
            } for i, e in enumerate(batch)]
            
            time.sleep(random.uniform(self.delay_min, self.delay_max))
            
            result, ok = self._call(items)
            if not ok:
                return [], len(batch)
            
            updates = []
            for r in result:
                tid = r.get('id', -1)
                if tid < 0 or tid >= len(batch):
                    continue
                
                text = r.get('translate', '')
                if self._valid(batch[tid]['original'], text):
                    updates.append((batch[tid]['id'], text))
            
            return updates, len(batch) - len(updates)
        except Exception as e:
            return [], len(batch)
    
    def _call(self, items: List[Dict]) -> Tuple[List[Dict], bool]:
        """Call API"""
        system = f"""You are a translator.
Translate from {self.source_lang} to {self.target_lang}.

RULES:
1. Preserve ALL variables EXACTLY: [var], {{var}}, %(var)s
2. Preserve special characters: \\n, \\", {{, }}
3. "context" is the speaking character - adapt speech style
4. Keep similar length for UI elements
5. Use natural {self.target_lang}

INPUT: [{{"id": 0, "context": "tara", "original": "Hello"}}, ...]
OUTPUT: [{{"id": 0, "translate": "Привет"}}, ...]
Return ONLY the JSON array, nothing else."""

        user = json.dumps(items, ensure_ascii=False)
        
        for attempt in range(self.retries):
            try:
                kwargs = {
                    'model': self.model,
                    'messages': [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user}
                    ],
                    'temperature': self.temperature,
                    'max_tokens': self.max_tokens
                }
                
                if self.reasoning:
                    kwargs['extra_body'] = {"reasoning": {"enabled": True}}
                
                resp = self.client.chat.completions.create(**kwargs)
                content = resp.choices[0].message.content.strip()
                
                parsed = self._parse(content, len(items))
                if parsed is not None:
                    return parsed, True
                
                if attempt < self.retries - 1:
                    time.sleep(1)
                    
            except Exception as e:
                if attempt == self.retries - 1:
                    return [], False
                time.sleep(2 ** attempt)
        
        return [], False
    
    def _parse(self, text: str, expected: int) -> List[Dict]:
        """Parse JSON response"""
        text = text.strip()
        if text.startswith('```'):
            lines = text.split('\n')
            lines = lines[1:]
            if lines and lines[-1].startswith('```'):
                lines = lines[:-1]
            text = '\n'.join(lines)
        
        start = text.find('[')
        end = text.rfind(']') + 1
        
        if start < 0 or end <= start:
            return None
        
        try:
            data = json.loads(text[start:end])
            if not isinstance(data, list):
                return None
            
            result = []
            seen = set()
            for item in data:
                if isinstance(item, dict) and 'id' in item and 'translate' in item:
                    tid = item['id']
                    if tid not in seen:
                        result.append({'id': tid, 'translate': str(item['translate'])})
                        seen.add(tid)
            
            for i in range(expected):
                if i not in seen:
                    result.append({'id': i, 'translate': ''})
            
            result.sort(key=lambda x: x['id'])
            return result[:expected]
        except json.JSONDecodeError:
            return None
    
    @staticmethod
    def _valid(original: str, translation: str) -> bool:
        """Validate translation"""
        if not translation or not translation.strip():
            return False
        
        vars_orig = set()
        for p in [r'\[.*?\]', r'\{.*?\}', r'%\(.*?\)[sd]']:
            vars_orig.update(re.findall(p, original))
        
        for v in vars_orig:
            if v not in translation:
                return False
        
        if re.search(r'\[\s*\]|\{\s*\}', translation):
            return False
        
        return True