"""
Base applier
"""

from core.database import TranslationDatabase


class BaseApplier:
    def __init__(self, config: dict, db: TranslationDatabase):
        self.config = config
        self.db = db
        self.source_lang = config['source']['lang']
        self.target_lang = config['source']['target_lang']
    
    def run(self, **kwargs):
        raise NotImplementedError