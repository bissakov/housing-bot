"""Persistent, fail-open translation of user-authored request descriptions."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from html import escape

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.i18n import normalize_language, t
from bot.models import Request, RequestTranslation
from bot.services.llm import get_llm

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalizedRequestDescription:
    original: str
    translation: str | None


async def localize_request_description(
    session: AsyncSession,
    request: Request,
    language: str | None,
    *,
    commit_immediately: bool = False,
) -> LocalizedRequestDescription:
    """Return one cached translation, creating it when necessary."""
    return (
        await localize_request_descriptions(
            session,
            [request],
            language,
            commit_immediately=commit_immediately,
        )
    )[request.id]


async def localize_request_descriptions(
    session: AsyncSession,
    requests: list[Request],
    language: str | None,
    *,
    commit_immediately: bool = False,
) -> dict[int, LocalizedRequestDescription]:
    """Translate a request batch concurrently and persist it in one short write.

    The caller owns the transaction. A newly generated row is flushed here and
    becomes durable with the caller's normal commit unless immediate persistence
    is requested by a read-only screen.
    """
    target_language = normalize_language(language)
    unique_requests = {request.id: request for request in requests}
    if not unique_requests:
        return {}
    originals = {
        request_id: request.description.strip()
        for request_id, request in unique_requests.items()
    }
    cached_rows = list((
        await session.execute(
            select(RequestTranslation).where(
                RequestTranslation.request_id.in_(unique_requests),
                RequestTranslation.target_language == target_language,
            )
        )
    ).scalars())
    translations = {
        row.request_id: row.translated_text for row in cached_rows
    }
    missing_ids = [
        request_id for request_id in unique_requests
        if request_id not in translations
    ]
    if not missing_ids:
        return {
            request_id: LocalizedRequestDescription(
                originals[request_id], translations[request_id]
            )
            for request_id in unique_requests
        }

    # SQLite cannot upgrade a read transaction after another connection writes.
    # Read-only screens therefore release their snapshot before waiting on the
    # LLM, then keep the eventual cache write as short as possible.
    if commit_immediately:
        await session.commit()

    llm = get_llm()
    if not llm.enabled:
        return {
            request_id: LocalizedRequestDescription(
                originals[request_id], translations.get(request_id)
            )
            for request_id in unique_requests
        }

    semaphore = asyncio.Semaphore(5)

    async def translate(request_id: int) -> tuple[int, str | None]:
        try:
            async with semaphore:
                value = await llm.translate_request(
                    originals[request_id], target_language
                )
            return request_id, value
        except Exception as exc:
            # The LLM client already records its diagnostic traceback.
            logger.warning(
                "request_translation_failed request_id=%s language=%s error=%s",
                request_id,
                target_language,
                exc,
            )
            return request_id, None

    generated = dict(await asyncio.gather(*(
        translate(request_id) for request_id in missing_ids
    )))
    translations.update({
        request_id: translated
        for request_id, translated in generated.items()
        if translated is not None
    })
    rows = [
        RequestTranslation(
            request_id=request_id,
            target_language=target_language,
            translated_text=translated,
        )
        for request_id, translated in generated.items()
        if translated is not None
    ]
    try:
        if rows:
            async with session.begin_nested():
                session.add_all(rows)
                await session.flush()
    except IntegrityError:
        # Another update may have filled this cache key while the LLM call ran.
        concurrent_rows = list((
            await session.execute(
                select(RequestTranslation).where(
                    RequestTranslation.request_id.in_(missing_ids),
                    RequestTranslation.target_language == target_language,
                )
            )
        ).scalars())
        translations.update({
            row.request_id: row.translated_text for row in concurrent_rows
        })
    except OperationalError as exc:
        # Cache persistence must never make a request unreadable. A later view
        # can retry saving this translation after the competing writer exits.
        logger.warning(
            "request_translation_cache_busy request_ids=%s language=%s error=%s",
            missing_ids,
            target_language,
            exc,
        )

    if commit_immediately:
        try:
            await session.commit()
        except OperationalError as exc:
            await session.rollback()
            logger.warning(
                "request_translation_commit_busy request_ids=%s language=%s error=%s",
                missing_ids,
                target_language,
                exc,
            )
    return {
        request_id: LocalizedRequestDescription(
            originals[request_id], translations.get(request_id)
        )
        for request_id in unique_requests
    }


def format_description_html(
    description: LocalizedRequestDescription,
    language: str | None,
    *,
    compact: bool = False,
    limit: int | None = None,
) -> str:
    """Render safe HTML with the translation first and original always shown."""
    original = description.original.replace("\n", " ") if compact else description.original
    translated = description.translation
    if translated and compact:
        translated = translated.replace("\n", " ")

    def clipped(value: str) -> str:
        if limit is not None and len(value) > limit:
            return value[:limit] + "…"
        return value

    original_html = escape(clipped(original))
    if not translated or translated.strip() == description.original.strip():
        return original_html if compact else (
            f"📝 <b>{t('original_description', language)}</b>\n{original_html}"
        )

    translated_html = escape(clipped(translated))
    if compact:
        return (
            f"🌐 <i>{translated_html}</i>\n"
            f"📝 {t('original_description', language)}: <i>{original_html}</i>"
        )
    return (
        f"🌐 <b>{t('ai_translation', language)}</b>\n{translated_html}\n\n"
        f"📝 <b>{t('original_description', language)}</b>\n{original_html}"
    )
