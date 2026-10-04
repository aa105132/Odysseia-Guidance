"""Optional server-side 2–8 player Texas search provider, no wallet access."""
import asyncio
from importlib import import_module
import json
import math
import os
from pathlib import Path
import secrets
import sys

search = import_module('src.chat.features.games.blackjack-web.texas_search')


class TexasSearchClient:
    def __init__(self, fallback):
        self.fallback = fallback
        self._use_evaluations = getattr(fallback, '_use_evaluations', False)
        self.texas_search_enabled = False
        self.search_timeout = 5.0
        self.simulations = 192
        self.python = os.getenv('GAME_TEXAS_SEARCH_PYTHON', sys.executable)
        self.deps = os.getenv('GAME_TEXAS_SEARCH_DEPS', '')
        self._semaphore = asyncio.Semaphore(1)
        try:
            self.search_timeout = float(os.getenv('GAME_TEXAS_SEARCH_TIMEOUT', '5'))
            self.simulations = int(os.getenv('GAME_TEXAS_SEARCH_SIMULATIONS', '192'))
            if not math.isfinite(self.search_timeout) or not 1 <= self.search_timeout <= 20:
                raise ValueError('Invalid deadline')
            if not 16 <= self.simulations <= 2048 or not Path(self.python).is_file():
                raise ValueError('Invalid runtime/budget')
            self.texas_search_enabled = os.getenv('GAME_TEXAS_SEARCH_ENABLED', 'false').lower() == 'true'
        except (ValueError, OSError):
            self.texas_search_enabled = False
        self.enabled = self.texas_search_enabled or fallback.enabled
        self.timeout_seconds = max(self.search_timeout if self.texas_search_enabled else 0, fallback.timeout_seconds)

    async def _run(self, request):
        async with self._semaphore:
            worker = Path(__file__).with_name('texas_search_worker.py')
            args = [self.python, '-I', str(worker)]
            if self.deps:
                args.append(self.deps)
            env = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
            proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env, limit=4096)
            try:
                output, _ = await proc.communicate(json.dumps(request).encode('utf8'))
                if proc.returncode or len(output) > 512:
                    return None
                return json.loads(output)
            finally:
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    await proc.wait()

    async def choose_action(self, context):
        if self.texas_search_enabled and context.get('game_type') == 'texas':
            try:
                request = dict(state=search.snapshot(context), simulations=self.simulations, seed=secrets.randbits(32))
                result = await asyncio.wait_for(self._run(request), self.search_timeout)
                llm = import_module('src.chat.features.games.blackjack-web.game_llm')
                return llm.GameLLMClient._validate_action(result, context)
            except (OSError, ValueError, TypeError, KeyError, StopIteration, asyncio.TimeoutError):
                return None
        if self.fallback.enabled:
            return await self.fallback.choose_action(context)
        return None
