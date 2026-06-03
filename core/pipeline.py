"""
Pipeline orchestrator
"""

import importlib
from core.database import TranslationDatabase

from extractors.base import BaseExtractor
from translators.base import BaseTranslator
from appliers.base import BaseApplier


class Pipeline:
    """Orchestrates extraction, translation, application"""
    
    def __init__(self, config: dict):
        self.config = config
        self.db = TranslationDatabase(config['paths']['database'])
        
        ext_name = config['pipeline']['extractor']
        tr_name = config['pipeline']['translator']
        app_name = config['pipeline']['applier']
        
        self.extractor = self._load('extractors', ext_name, BaseExtractor)
        self.translator = self._load('translators', tr_name, BaseTranslator)
        self.applier = self._load('appliers', app_name, BaseApplier)
    
    def _load(self, module_type: str, name: str, base_class):
        """Dynamically load module, find class that inherits from base_class"""
        module = importlib.import_module(f"{module_type}.{name}")
        
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            
            # Проверяем что это класс, наследуется от base_class, и не сам base_class
            if (isinstance(attr, type) and 
                issubclass(attr, base_class) and 
                attr is not base_class):
                
                return attr(self.config, self.db)
        
        raise ImportError(
            f"No class inheriting from {base_class.__name__} found in {module_type}.{name}"
        )
    
    def extract(self, **kwargs):
        return self.extractor.run(**kwargs)
    
    def translate(self, **kwargs):
        return self.translator.run(**kwargs)
    
    def apply(self, **kwargs):
        return self.applier.run(**kwargs)
    
    def run_all(self, **kwargs):
        print("=" * 50)
        print("LATTE Pipeline")
        print("=" * 50)
        
        print(f"\n[1/3] EXTRACT")
        self.extract(**kwargs)
        
        print(f"\n[2/3] TRANSLATE")
        self.translate(**kwargs)
        
        print(f"\n[3/3] APPLY")
        self.apply(**kwargs)
        
        print(f"\n{'=' * 50}")
        print("Done!")
        print(f"{'=' * 50}")