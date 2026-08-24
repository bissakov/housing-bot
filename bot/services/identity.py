"""Resolve the business actor behind an incoming Telegram update."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import DEV_MODE
from bot.models import DevPersona, DevSession, User


async def get_actor(session: AsyncSession, telegram_id: int) -> User | None:
    """Return the selected DEV persona or the account's ordinary user."""
    if DEV_MODE:
        persona = await session.scalar(
            select(User)
            .join(DevPersona, DevPersona.user_id == User.id)
            .join(DevSession, DevSession.persona_id == DevPersona.id)
            .where(DevSession.controller_telegram_id == telegram_id)
        )
        if persona is not None:
            return persona
    return await session.scalar(
        select(User).where(User.telegram_id == telegram_id)
    )


async def sync_persona_languages(
    session: AsyncSession, controller_telegram_id: int, language: str
) -> None:
    """Keep every DEV persona on its controller's account-wide locale."""
    if not DEV_MODE:
        return
    await session.execute(
        update(User)
        .where(
            User.id.in_(
                select(DevPersona.user_id).where(
                    DevPersona.controller_telegram_id == controller_telegram_id
                )
            )
        )
        .values(language=language)
        .execution_options(synchronize_session=False)
    )


async def delivery_telegram_id(
    session: AsyncSession, user: User, *, active_only: bool = False
) -> int | None:
    """Route a DEV persona notification to the developer controlling it."""
    if DEV_MODE:
        row = (
            await session.execute(
                select(
                    DevPersona.controller_telegram_id,
                    DevSession.controller_telegram_id,
                )
                .outerjoin(
                    DevSession, DevSession.persona_id == DevPersona.id
                )
                .where(DevPersona.user_id == user.id)
            )
        ).one_or_none()
        if row is not None:
            controller_id, active_controller_id = row
            if active_only and active_controller_id is None:
                return None
            return controller_id
    return user.telegram_id
