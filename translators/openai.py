"""
OpenAI translator (async, proxy-aware)
"""

import asyncio
import json
import random
import re
from typing import List, Dict, Tuple, Optional

from tqdm import tqdm

from translators.base import BaseTranslator

try:
    from openai import AsyncOpenAI
    import openai as openai_module
    _HAVE_OPENAI = True
except ImportError:
    AsyncOpenAI = None
    openai_module = None
    _HAVE_OPENAI = False

try:
    import httpx
    _HAVE_HTTPX = True
except ImportError:
    httpx = None
    _HAVE_HTTPX = False


class TranslationError(Exception):
    """Ошибка API с человекочитаемой причиной."""

    def __init__(self, kind: str, message: str,
                 status: Optional[int] = None,
                 preview: str = ''):
        super().__init__(message)
        self.kind = kind          # 'auth' | 'rate_limit' | 'bad_request' | ...
        self.status = status
        self.message = message
        self.preview = preview

    def signature(self) -> tuple:
        """Ключ для агрегации одинаковых ошибок."""
        return (self.kind, self.status, self.message[:120])

    def __str__(self):
        tag = f"[{self.kind}"
        if self.status:
            tag += f" {self.status}"
        tag += "]"
        return f"{tag} {self.message}"


class OpenaiTranslator(BaseTranslator):
    """OpenAI-based translator (async, proxy-aware)."""

    HINTS = {
        'auth':            '→ проверь api_key в конфиге',
        'permission':      '→ у ключа нет доступа к этой модели',
        'model_not_found': '→ указанной модели нет у провайдера',
        'rate_limit':      '→ превышен лимит, уменьши workers или увеличь delay',
        'network':         '→ проверь base_url и соединение',
        'proxy':           '→ проверь proxy в конфиге (http://, https://, socks5://)',
        'timeout':         '→ сервер слишком долго отвечает',
        'parse':           '→ модель не вернула JSON, попробуй другую',
        'bad_request':     '→ вероятно, несовместимый reasoning-параметр',
    }

    def __init__(self, config: dict, db):
        super().__init__(config, db)

        if not _HAVE_OPENAI:
            raise ImportError("pip install openai")

        self.proxy = self.cfg.get('proxy') or None
        self.timeout = self.cfg.get('timeout', 60.0)

        self._http_client = self._build_http_client()

        # max_retries=0 — ретраями управляем сами; timeout — чтобы не висло.
        self.client = AsyncOpenAI(
            api_key=self.cfg['api_key'],
            base_url=self.cfg.get('base_url') or None,
            max_retries=0,
            timeout=self.timeout,
            http_client=self._http_client,
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
        self.reasoning_effort = self.cfg.get('reasoning_effort', 'minimal')

    # ------------------------------------------------------------------ #
    #  HTTP-клиент с прокси                                              #
    # ------------------------------------------------------------------ #

    def _build_http_client(self):
        """Async httpx-клиент с прокси и таймаутом."""
        if not _HAVE_HTTPX:
            if self.proxy:
                raise ImportError(
                    "pip install httpx (требуется для proxy)"
                )
            # OpenAI SDK притащит httpx сам, отдадим None — пусть решает сам
            return None

        kwargs = {
            'timeout': httpx.Timeout(self.timeout),
        }

        if self.proxy:
            # httpx понимает http://, https://, socks5:// (для socks нужен httpx[socks])
            kwargs['proxy'] = self.proxy
            print(f"Proxy: {self.proxy}")

        return httpx.AsyncClient(**kwargs)

    async def _aclose(self) -> None:
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Публичный синхронный интерфейс                                    #
    # ------------------------------------------------------------------ #

    def run(self, dry_run: bool = False, **kwargs) -> dict:
        try:
            return asyncio.run(self._run_async(dry_run=dry_run))
        finally:
            # asyncio.run закрывает loop — httpx.AsyncClient нужно закрыть до этого.
            # Поэтому закрываем внутри корутины (см. _run_async), а здесь — на случай
            # если run падает до входа в _run_async.
            pass

    # ------------------------------------------------------------------ #
    #  Основной цикл                                                     #
    # ------------------------------------------------------------------ #

    async def _run_async(self, dry_run: bool = False) -> dict:
        try:
            return await self._run_impl(dry_run=dry_run)
        finally:
            await self._aclose()

    async def _run_impl(self, dry_run: bool = False) -> dict:
        print(f"\n{self.source_lang} → {self.target_lang}")
        print(f"Model: {self.model}, Workers: {self.workers}, Batch: {self.batch_size}")

        s = self.db.stats()
        print(f"Untranslated: {s['untranslated']}")

        if s['untranslated'] == 0:
            print("Nothing to translate!")
            return s

        if dry_run:
            for i, e in enumerate(
                self.db.get_untranslated(self.source_lang, self.target_lang, 10), 1
            ):
                ctx = f" [{e['context']}]" if e.get('context') else ""
                print(f"  {i}.{ctx} {e['original'][:80]}")
            return s

        total = 0
        round_num = 0
        errors: Dict[tuple, Dict] = {}
        last_done = 0

        while round_num < 10:
            round_num += 1

            untranslated = self.db.get_untranslated(self.source_lang, self.target_lang)
            if not untranslated:
                print("\n✓ Done!")
                break

            print(f"\nRound {round_num}: {len(untranslated)} remaining")

            batches = [
                untranslated[i:i + self.batch_size]
                for i in range(0, len(untranslated), self.batch_size)
            ]

            done = 0
            sem = asyncio.Semaphore(self.workers)

            async def guarded(batch):
                async with sem:
                    return await self._batch(batch)

            tasks = [asyncio.create_task(guarded(b)) for b in batches]

            with tqdm(total=len(batches), desc="Translating") as pbar:
                for coro in asyncio.as_completed(tasks):
                    try:
                        updates, _ = await coro
                        if updates:
                            self.db.update_batch(updates)
                        done += len(updates)
                        total += len(updates)
                        pbar.set_postfix({'done': total})
                    except TranslationError as e:
                        self._record_error(errors, e, pbar)
                    except Exception as e:
                        self._record_error(
                            errors,
                            TranslationError('unknown', str(e)),
                            pbar,
                        )
                    pbar.update(1)

            last_done = done
            print(f"  Done: {done}")
            if done == 0:
                break

        if errors:
            self._print_summary(errors, last_done)

        return self.db.stats()

    # ------------------------------------------------------------------ #
    #  Обработка ошибок                                                  #
    # ------------------------------------------------------------------ #

    def _record_error(self, errors: Dict, e: TranslationError, pbar) -> None:
        """Инкрементирует счётчик ошибки; новую — печатает сразу."""
        sig = e.signature()
        if sig in errors:
            errors[sig]['count'] += 1
            return

        errors[sig] = {'count': 1, 'err': e, 'sample': e.preview}
        line = f"  ⚠ {e}"
        if e.preview:
            line += f"  пример: {e.preview!r}"
        # tqdm.write не ломает прогресс-бар
        tqdm.write(line)

    def _print_summary(self, errors: Dict, done: int) -> None:
        print("\n⚠ Итог по ошибкам:")
        for _, info in sorted(errors.items(), key=lambda kv: -kv[1]['count']):
            e = info['err']
            print(f"  ×{info['count']:>4}  {e}")
            if info['sample']:
                print(f"          пример: {info['sample']!r}")

        kinds = {info['err'].kind for info in errors.values()}
        for kind in kinds & self.HINTS.keys():
            print(f"  {self.HINTS[kind]}")

        if done == 0 and len(errors) == 1:
            print("\nПрерываю: все запросы падают одинаково.")

    def _wrap_error(self, e: Exception) -> TranslationError:
        """Превращаем исключение SDK в понятную причину."""
        # Прокси-ошибки httpx
        if httpx is not None:
            if isinstance(e, httpx.ProxyError):
                return TranslationError('proxy', f"proxy error: {e}")
            if isinstance(e, httpx.ConnectError):
                return TranslationError('network', f"connect failed: {e}")
            if isinstance(e, httpx.ReadTimeout):
                return TranslationError('timeout', 'read timed out')
            if isinstance(e, httpx.HTTPError):
                return TranslationError('network', str(e))

        if openai_module is not None:
            if isinstance(e, openai_module.AuthenticationError):
                return TranslationError('auth', 'invalid API key', 401)
            if isinstance(e, openai_module.PermissionDeniedError):
                return TranslationError('permission', 'access denied', 403)
            if isinstance(e, openai_module.NotFoundError):
                return TranslationError(
                    'model_not_found', f"model '{self.model}' not found", 404,
                )
            if isinstance(e, openai_module.RateLimitError):
                return TranslationError('rate_limit', 'quota exceeded', 429)
            if isinstance(e, openai_module.APITimeoutError):
                return TranslationError('timeout', 'request timed out')
            if isinstance(e, openai_module.APIConnectionError):
                # внутри APIConnectionError может сидеть httpx.ProxyError
                cause = getattr(e, '__cause__', None)
                if httpx is not None and isinstance(cause, httpx.ProxyError):
                    return TranslationError('proxy', f"proxy error: {cause}")
                return TranslationError('network', str(e))
            if isinstance(e, openai_module.APIStatusError):
                body = getattr(e, 'body', None)
                msg = body.get('message', str(e)) if isinstance(body, dict) else str(e)
                return TranslationError(f'http_{e.status_code}', msg, e.status_code)

        return TranslationError('unknown', str(e))

    # ------------------------------------------------------------------ #
    #  Один батч                                                         #
    # ------------------------------------------------------------------ #

    async def _batch(self, batch: List[Dict]) -> Tuple[List, int]:
        items = [{
            'id': i,
            'context': e.get('context', ''),
            'original': e['original'],
        } for i, e in enumerate(batch)]

        await asyncio.sleep(random.uniform(self.delay_min, self.delay_max))

        try:
            result = await self._call(items)
        except TranslationError as e:
            if batch:
                e.preview = batch[0]['original'][:80]
            raise

        updates = []
        for r in result:
            tid = r.get('id', -1)
            if tid < 0 or tid >= len(batch):
                continue
            text = r.get('translate', '')
            if self._valid(batch[tid]['original'], text):
                updates.append((batch[tid]['id'], text))

        return updates, len(batch) - len(updates)

    # ------------------------------------------------------------------ #
    #  Управление reasoning                                              #
    # ------------------------------------------------------------------ #

    def _reasoning_kwargs(self) -> dict:
        """Собираем параметры управления reasoning под известные провайдеры.
        Если провайдер не распознан — шлём все варианты сразу."""
        on = self.reasoning
        base = (self.cfg.get('base_url') or '').lower()

        # OpenRouter
        if 'openrouter.ai' in base:
            return {'extra_body': {'reasoning': {'enabled': on}}}

        # DashScope (Qwen)
        if 'dashscope' in base or 'aliyuncs.com' in base:
            return {'extra_body': {'enable_thinking': on}}

        # OpenAI (o1/o3/gpt-5)
        if 'api.openai.com' in base:
            return {'reasoning_effort': self.reasoning_effort}

        # Google Gemini (OpenAI-compat)
        if 'generativelanguage' in base or 'googleapis.com' in base:
            return {'extra_body': {
                'thinking_config': {'thinking_budget': -1 if on else 0}
            }}

        # DeepSeek — управляется только выбором модели, параметров нет
        if 'api.deepseek.com' in base:
            return {}

        # Anthropic — thinking выключен по умолчанию, ничего не передаём
        if 'api.anthropic.com' in base:
            return {}

        # Неизвестный провайдер — шлём все варианты разом,
        # лишние поля сервер, как правило, игнорирует.
        return {'extra_body': {
            'reasoning':         {'enabled': on},
            'enable_thinking':   on,
            'thinking_config':   {'thinking_budget': -1 if on else 0},
            'reasoning_effort':  self.reasoning_effort,
        }}

    # ------------------------------------------------------------------ #
    #  Вызов API                                                         #
    # ------------------------------------------------------------------ #

    async def _call(self, items: List[Dict]) -> List[Dict]:
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
        reasoning_kwargs = self._reasoning_kwargs()

        last_error = TranslationError('unknown', 'no attempts made')

        for attempt in range(self.retries):
            try:
                kwargs = {
                    'model': self.model,
                    'messages': [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                    'temperature': self.temperature,
                    'max_tokens': self.max_tokens,
                    **reasoning_kwargs,
                }

                try:
                    resp = await self.client.chat.completions.create(**kwargs)
                except Exception:
                    # Провайдер не проглотил reasoning-параметры — повторяем без них
                    if reasoning_kwargs:
                        for k in reasoning_kwargs:
                            kwargs.pop(k, None)
                        reasoning_kwargs = {}
                        resp = await self.client.chat.completions.create(**kwargs)
                    else:
                        raise

                content = (resp.choices[0].message.content or '').strip()
                parsed = self._parse(content, len(items))
                if parsed is not None:
                    return parsed

                last_error = TranslationError(
                    'parse', 'model returned invalid JSON',
                )

            except Exception as e:
                last_error = self._wrap_error(e)

            if attempt < self.retries - 1:
                await asyncio.sleep(2 ** attempt)

        raise last_error

    # ------------------------------------------------------------------ #
    #  Парсинг и валидация                                               #
    # ------------------------------------------------------------------ #

    def _parse(self, text: str, expected: int) -> Optional[List[Dict]]:
        text = text.strip()
        if text.startswith('```'):
            lines = text.split('\n')[1:]
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
        if not translation or not translation.strip():
            return False

        vars_orig = set()
        # Ren'Py переменные
        for p in [r'\[.*?\]', r'\{.*?\}', r'%\(.*?\)[sd]']:
            vars_orig.update(re.findall(p, original))
        # Twine переменные
        vars_orig.update(re.findall(r'\$[a-zA-Z_]\w*', original))

        for v in vars_orig:
            if v not in translation:
                return False

        if re.search(r'\[\s*\]|\{\s*\}', translation):
            return False

        return True