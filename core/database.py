"""
Minimal translation database
"""

import sqlite3
from typing import List, Dict, Optional, Tuple


class TranslationDatabase:
    """Minimal SQLite cache"""
    
    def __init__(self, db_path: str = "translations.db"):
        self.db_path = db_path
        self._init()
    
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init(self):
        conn = self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original TEXT NOT NULL,
                translation TEXT,
                source_lang TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                context TEXT DEFAULT '',
                UNIQUE(original, context, source_lang, target_lang)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_untranslated
            ON translations(source_lang, target_lang) 
            WHERE translation IS NULL OR translation = ''
        """)
        conn.commit()
        conn.close()
    
    def insert_batch(self, entries: List[Dict]) -> int:
        """Insert entries. Returns count added."""
        conn = self._connect()
        count = 0
        
        for e in entries:
            if e:
                try:
                    conn.execute("""
                        INSERT INTO translations 
                        (original, translation, source_lang, target_lang, context)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        e['original'],
                        e.get('translation'),
                        e['source_lang'],
                        e['target_lang'],
                        e.get('context', '')
                    ))
                    count += 1
                except sqlite3.IntegrityError:
                    pass
        
        conn.commit()
        conn.close()
        return count
    
    def get_untranslated(self, source_lang: str, target_lang: str,
                        limit: int = None) -> List[Dict]:
        """Get untranslated entries"""
        conn = self._connect()
        
        query = """
            SELECT id, original, context
            FROM translations 
            WHERE source_lang = ? AND target_lang = ? 
            AND (translation IS NULL OR translation = '')
            ORDER BY id
        """
        params = [source_lang, target_lang]
        
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        
        result = [dict(row) for row in conn.execute(query, params)]
        conn.close()
        return result
    
    def get_translated(self, source_lang: str, target_lang: str) -> List[Dict]:
        """Get translated entries"""
        conn = self._connect()
        result = [dict(row) for row in conn.execute("""
            SELECT original, translation, context
            FROM translations 
            WHERE source_lang = ? AND target_lang = ? 
            AND translation IS NOT NULL AND translation != ''
            ORDER BY id
        """, (source_lang, target_lang))]
        conn.close()
        return result
    
    def update_batch(self, updates: List[Tuple[int, str]]) -> int:
        """Update translations. Returns count updated."""
        if not updates:
            return 0
        
        conn = self._connect()
        count = 0
        
        for eid, text in updates:
            conn.execute(
                "UPDATE translations SET translation = ? WHERE id = ?",
                (text, eid)
            )
            if conn.total_changes > 0:
                count += 1
        
        conn.commit()
        conn.close()
        return count
    
    def stats(self) -> Dict:
        """Get statistics"""
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
        translated = conn.execute(
            "SELECT COUNT(*) FROM translations WHERE translation IS NOT NULL AND translation != ''"
        ).fetchone()[0]
        conn.close()
        
        return {
            'total': total,
            'translated': translated,
            'untranslated': total - translated,
            'completion': (translated / total * 100) if total > 0 else 0
        }
    
    def clear(self):
        conn = self._connect()
        conn.execute("DELETE FROM translations")
        conn.commit()
        conn.close()
    
    def vacuum(self):
        conn = self._connect()
        conn.execute("VACUUM")
        conn.close()