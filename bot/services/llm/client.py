from __future__ import annotations
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

from .prompts import CLASSIFY_PROMPT, POLISH_PROMPT, TRIAGE_PROMPT
from bot.constants import REQUEST_CATEGORIES

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT = 8
DEFAULT_BASE_URL = "https://api.openai.com/v1"
VALID_CATEGORIES = REQUEST_CATEGORIES

@dataclass
class ClassifyResult:
    category: str
    confidence: float
    enriched: str
    urgency: str  # low|normal|high
    reason: str = ""

@dataclass
class TriageResult:
    priority: str
    summary: str
    hint: str

@dataclass
class PolishResult:
    text: str

class LLMClient:
    """OpenAI-compatible Chat Completions client (openai / proxy / local vLLM)."""
    def __init__(self, api_key: str = "", base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL, timeout: int = DEFAULT_TIMEOUT, enabled: bool = True):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        self._enabled = enabled and bool(self.api_key)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def _chat(self, messages: list[dict], temperature: float = 0.2, max_tokens: int = 600, json_mode: bool = False) -> str:
        if not self.enabled:
            raise RuntimeError("LLM disabled (no API key or LLM_ENABLED=false)")
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload: dict = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        t0 = time.monotonic()
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.post(url, headers=headers, json=payload) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        raise RuntimeError(f"LLM HTTP {resp.status}: {body[:500]}")
                    data = json.loads(body)
                    content = data["choices"][0]["message"]["content"] or ""
                    log.info("llm ok model=%s dt=%.2fs len=%d", self.model, time.monotonic()-t0, len(content))
                    return content.strip()
        except Exception:
            log.exception("llm_request_failed model=%s dt=%.2fs", self.model, time.monotonic()-t0)
            raise

    def _parse_json(self, text: str) -> dict:
        # strip ```json fences if any
        t = text.strip()
        if t.startswith("```"):
            # find first { and last }
            s = t.find("{")
            e = t.rfind("}")
            if s != -1 and e != -1:
                t = t[s:e+1]
        try:
            return json.loads(t)
        except Exception:
            # try extract json substring
            s = t.find("{")
            e = t.rfind("}")
            if s != -1 and e != -1:
                return json.loads(t[s:e+1])
            raise

    async def classify_and_enrich(self, raw_text: str) -> ClassifyResult:
        raw = (raw_text or "").strip()
        if len(raw) < 2:
            return ClassifyResult(category="plumber", confidence=0.0, enriched=raw, urgency="normal", reason="too short")
        content = await self._chat(
            [{"role": "system", "content": CLASSIFY_PROMPT}, {"role": "user", "content": raw}],
            temperature=0.2, max_tokens=400, json_mode=True
        )
        d = self._parse_json(content)
        cat = str(d.get("category", "")).strip().lower()
        if cat not in VALID_CATEGORIES:
            cat = ""
        try:
            conf = float(d.get("confidence", 0.5))
        except Exception:
            conf = 0.5
        conf = max(0.0, min(1.0, conf))
        if not cat:
            conf = 0.0
        enriched = str(d.get("enriched", raw)).strip() or raw
        urgency = str(d.get("urgency", "normal")).lower()
        if urgency not in ("low", "normal", "high"):
            urgency = "normal"
        reason = str(d.get("reason", ""))[:100]
        # clamp enriched length
        if len(enriched) > 800:
            enriched = enriched[:800]
        return ClassifyResult(category=cat, confidence=conf, enriched=enriched, urgency=urgency, reason=reason)

    async def triage(self, description: str, category: str) -> TriageResult:
        prompt = TRIAGE_PROMPT.format(description=description[:800], category=category)
        content = await self._chat(
            [{"role": "system", "content": "Верни только JSON."}, {"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=300, json_mode=True
        )
        d = self._parse_json(content)
        pri = str(d.get("priority", "normal")).lower()
        if pri not in ("low", "normal", "high"):
            pri = "normal"
        return TriageResult(priority=pri, summary=str(d.get("summary",""))[:200], hint=str(d.get("hint",""))[:200])

    async def polish(self, text: str) -> PolishResult:
        raw = (text or "").strip()
        if not raw:
            return PolishResult(text=raw)
        content = await self._chat(
            [{"role": "user", "content": POLISH_PROMPT + raw}],
            temperature=0.4, max_tokens=700, json_mode=False
        )
        # strip quotes if wrapped
        t = content.strip().strip('"').strip("'").strip()
        if len(t) > 1000:
            t = t[:1000]
        return PolishResult(text=t or raw)


# global singleton
_client: Optional[LLMClient] = None

def init_llm(api_key: str = "", base_url: str = "", model: str = "", timeout: int = 0, enabled: bool | None = None) -> LLMClient:
    global _client
    from bot import config as cfg
    ak = api_key if api_key != "" else getattr(cfg, "LLM_API_KEY", "")
    bu = base_url if base_url != "" else getattr(cfg, "LLM_BASE_URL", DEFAULT_BASE_URL)
    mo = model if model != "" else getattr(cfg, "LLM_MODEL", DEFAULT_MODEL)
    to = timeout if timeout else getattr(cfg, "LLM_TIMEOUT", DEFAULT_TIMEOUT)
    en = enabled if enabled is not None else getattr(cfg, "LLM_ENABLED", False)
    # allow enabling without key for tests (mocked _chat) -> but client.enabled will be False if no key
    # tests can patch _chat directly
    _client = LLMClient(api_key=ak, base_url=bu or DEFAULT_BASE_URL, model=mo or DEFAULT_MODEL, timeout=to or DEFAULT_TIMEOUT, enabled=bool(en))
    return _client

def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = init_llm()
    return _client
