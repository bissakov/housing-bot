from __future__ import annotations
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

from .prompts import CLASSIFY_PROMPT, DUPLICATE_PROMPT, POLISH_PROMPT, TRIAGE_PROMPT
from bot.constants import REQUEST_CATEGORIES

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT = 8
DEFAULT_BASE_URL = "https://api.openai.com/v1"
VALID_CATEGORIES = REQUEST_CATEGORIES

# Hard ceiling on any user-authored text handed to the API. Telegram allows 4096
# chars per message; without this a single message can burn unbounded tokens.
MAX_INPUT_CHARS = 1200

DECISIONS = ("accept", "needs_detail", "off_topic")

DEFAULT_OFF_TOPIC = (
    "Я принимаю только заявки по дому: сантехника, электрика, отопление, "
    "лифт, подъезд, безопасность. Опишите, пожалуйста, проблему по дому."
)
DEFAULT_NEEDS_DETAIL = (
    "Не хватает деталей. Напишите, что именно случилось и где "
    "(комната, этаж, подъезд)."
)


@dataclass
class ClassifyResult:
    category: str
    confidence: float
    enriched: str
    urgency: str  # low|normal|high
    reason: str = ""
    decision: str = "accept"  # accept|needs_detail|off_topic
    follow_up: str = ""  # message shown to the resident when not accepted

    @property
    def ok(self) -> bool:
        return self.decision == "accept"


@dataclass
class TriageResult:
    priority: str
    summary: str
    hint: str


@dataclass
class DuplicateResult:
    decision: str  # unique|duplicate|needs_clarification
    duplicate_request_id: int | None
    confidence: float
    question: str = ""
    reason: str = ""

@dataclass
class PolishResult:
    text: str
    off_topic: bool = False


class LLMClient:
    """OpenAI-compatible Chat Completions client (openai / proxy / local vLLM)."""
    def __init__(self, api_key: str = "", base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT, enabled: bool = True):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = float(timeout)
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
                    usage = data.get("usage") or {}
                    log.info(
                        "llm ok model=%s dt=%.2fs len=%d prompt_tokens=%s completion_tokens=%s",
                        self.model, time.monotonic()-t0, len(content),
                        usage.get("prompt_tokens", "?"), usage.get("completion_tokens", "?"),
                    )
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

    async def classify_and_enrich(self, raw_text: str, category: str | None = None) -> ClassifyResult:
        """Scope-check, classify and rewrite a resident's problem description.

        ``category`` pins a category the resident already picked by hand; the call
        is then only a ЖКХ scope check + quality gate + rewrite.
        """
        raw = (raw_text or "").strip()[:MAX_INPUT_CHARS]
        if len(raw) < 2:
            return ClassifyResult(
                category="", confidence=0.0, enriched=raw, urgency="normal",
                reason="too short", decision="needs_detail", follow_up=DEFAULT_NEEDS_DETAIL,
            )
        pinned = category if category in VALID_CATEGORIES else None
        # User text travels as a JSON field, never concatenated into the instructions.
        payload = json.dumps({"text": raw, "known_category": pinned}, ensure_ascii=False)
        content = await self._chat(
            [{"role": "system", "content": CLASSIFY_PROMPT}, {"role": "user", "content": payload}],
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

        decision = str(d.get("decision", "")).strip().lower()
        if decision not in DECISIONS:
            # Older/sloppier models may omit the field: infer it conservatively.
            decision = "accept" if cat else "needs_detail"

        enriched = str(d.get("enriched", raw)).strip() or raw
        urgency = str(d.get("urgency", "normal")).lower()
        if urgency not in ("low", "normal", "high"):
            urgency = "normal"
        reason = str(d.get("reason", ""))[:100]
        follow_up = str(d.get("follow_up", "")).strip()[:300]
        if len(enriched) > 800:
            enriched = enriched[:800]

        if pinned:
            # The resident's own choice wins over the model's guess.
            cat, conf = pinned, 1.0
        if decision == "accept" and not cat:
            # No category means nothing to file against: treat as under-specified.
            decision = "needs_detail"
        if decision != "accept":
            # Never leak a model-authored rewrite for text we are rejecting.
            enriched, conf = raw, 0.0
            if decision == "off_topic":
                cat = ""
            follow_up = follow_up or (
                DEFAULT_OFF_TOPIC if decision == "off_topic" else DEFAULT_NEEDS_DETAIL
            )
        if not cat:
            conf = 0.0
        return ClassifyResult(
            category=cat, confidence=conf, enriched=enriched, urgency=urgency,
            reason=reason, decision=decision, follow_up=follow_up,
        )

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

    async def check_duplicate(
        self,
        description: str,
        category: str,
        candidates: list[dict],
        clarification: str = "",
    ) -> DuplicateResult:
        """Compare a draft with a bounded set of active requests.

        Candidate selection is deliberately done by the application. The model
        cannot nominate an arbitrary request id, and malformed/overconfident
        answers are downgraded to clarification rather than blocking creation.
        """
        allowed_ids: set[int] = set()
        for candidate in candidates[:12]:
            if not isinstance(candidate, dict):
                continue
            try:
                allowed_ids.add(int(candidate["id"]))
            except (KeyError, TypeError, ValueError):
                continue
        if not allowed_ids:
            return DuplicateResult("unique", None, 1.0, reason="no active candidates")

        safe_candidates = []
        for candidate in candidates[:12]:
            try:
                request_id = int(candidate["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if request_id not in allowed_ids:
                continue
            safe_candidates.append({
                "id": request_id,
                "text": str(candidate.get("text", ""))[:400],
                "same_resident": bool(candidate.get("same_resident", False)),
            })

        payload = json.dumps(
            {
                "new_request": {
                    "text": (description or "").strip()[:MAX_INPUT_CHARS],
                    "category": category if category in VALID_CATEGORIES else "",
                },
                "candidates": safe_candidates,
                "clarification": (clarification or "").strip()[:800],
            },
            ensure_ascii=False,
        )
        content = await self._chat(
            [
                {"role": "system", "content": DUPLICATE_PROMPT},
                {"role": "user", "content": payload},
            ],
            temperature=0.1,
            max_tokens=300,
            json_mode=True,
        )
        d = self._parse_json(content)
        decision = str(d.get("decision", "needs_clarification")).strip().lower()
        if decision not in ("unique", "duplicate", "needs_clarification"):
            decision = "needs_clarification"
        try:
            confidence = max(0.0, min(1.0, float(d.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            duplicate_id = int(d.get("duplicate_request_id"))
        except (TypeError, ValueError):
            duplicate_id = None
        if duplicate_id not in allowed_ids:
            duplicate_id = None
            if decision == "duplicate":
                decision = "needs_clarification"
        if decision == "unique":
            duplicate_id = None
        question = str(d.get("question", "")).strip()[:300]
        if decision != "unique" and not question:
            question = "Чем эта проблема отличается от уже поданной заявки и где именно она возникла?"
        return DuplicateResult(
            decision=decision,
            duplicate_request_id=duplicate_id,
            confidence=confidence,
            question=question,
            reason=str(d.get("reason", ""))[:120],
        )

    async def polish(self, text: str) -> PolishResult:
        raw = (text or "").strip()
        if not raw:
            return PolishResult(text=raw)
        raw = raw[:MAX_INPUT_CHARS]
        content = await self._chat(
            [
                {"role": "system", "content": POLISH_PROMPT},
                {"role": "user", "content": json.dumps({"text": raw}, ensure_ascii=False)},
            ],
            temperature=0.4, max_tokens=700, json_mode=False
        )
        off_topic = False
        try:
            d = self._parse_json(content)
            t = str(d.get("text", "")).strip()
            off_topic = bool(d.get("off_topic", False))
        except Exception:
            # Model ignored the JSON contract: fall back to treating it as plain text.
            t = content.strip().strip('"').strip("'").strip()
        if off_topic:
            return PolishResult(text=raw, off_topic=True)
        if len(t) > 1000:
            t = t[:1000]
        return PolishResult(text=t or raw)


# global singleton
_client: Optional[LLMClient] = None

def init_llm(api_key: str = "", base_url: str = "", model: str = "", timeout: float = 0, enabled: bool | None = None) -> LLMClient:
    global _client
    from bot import config as cfg
    ak = api_key if api_key != "" else getattr(cfg, "LLM_API_KEY", "")
    bu = base_url if base_url != "" else getattr(cfg, "LLM_BASE_URL", DEFAULT_BASE_URL)
    mo = model if model != "" else getattr(cfg, "LLM_MODEL", DEFAULT_MODEL)
    # config exposes LLM_TIMEOUT_SECONDS; LLM_TIMEOUT kept as a legacy alias.
    to = timeout if timeout else getattr(
        cfg, "LLM_TIMEOUT_SECONDS", getattr(cfg, "LLM_TIMEOUT", DEFAULT_TIMEOUT)
    )
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
