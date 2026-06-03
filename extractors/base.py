"""
Base extractor
"""

from core.database import TranslationDatabase


class BaseExtractor:
    def __init__(self, config: dict, db: TranslationDatabase):
        self.config = config
        self.db = db
        self.source_lang = config['source']['lang']
        self.target_lang = config['source']['target_lang']
    
    def run(self, **kwargs):
        raise NotImplementedError
    
    def _entry(self, original: str, translation: str = None,
              context: str = '') -> dict:
        return {
            'original': original,
            'translation': translation,
            'source_lang': self.source_lang,
            'target_lang': self.target_lang,
            'context': context,
        }