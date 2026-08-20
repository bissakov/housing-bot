import pytest
from sqlalchemy import select

from bot.auth import is_approved_owner
from bot.handlers.resident import _tenant_management_view
from bot.keyboards import resident_menu
from bot.models import User


def test_owner_menu_has_tenant_management_button():
    normal_labels = {
        button.text for row in resident_menu("ru").keyboard for button in row
    }
    owner_labels = {
        button.text
        for row in resident_menu("ru", is_owner=True).keyboard
        for button in row
    }
    assert "🔑 Арендатор" not in normal_labels
    assert "🔑 Арендатор" in owner_labels


def test_is_approved_owner_requires_owner_subrole():
    owner = User(
        telegram_id=1, role="resident", resident_subrole="owner", is_approved=True
    )
    tenant = User(
        telegram_id=2, role="resident", resident_subrole="tenant", is_approved=True
    )
    assert is_approved_owner(owner)
    assert not is_approved_owner(tenant)


@pytest.mark.asyncio
async def test_owner_only_sees_tenants_for_own_apartment(session):
    owner = User(
        telegram_id=10,
        full_name="Owner",
        apartment="12",
        role="resident",
        resident_subrole="owner",
        is_approved=True,
    )
    own_tenant = User(
        telegram_id=11,
        full_name="Own tenant",
        apartment="12",
        role="resident",
        resident_subrole="tenant",
        is_approved=False,
    )
    other_tenant = User(
        telegram_id=12,
        full_name="Other tenant",
        apartment="13",
        role="resident",
        resident_subrole="tenant",
        is_approved=False,
    )
    session.add_all([owner, own_tenant, other_tenant])
    await session.commit()

    text, markup = await _tenant_management_view(session, owner)

    assert "Own tenant" in markup.inline_keyboard[0][0].text
    assert all(
        "Other tenant" not in button.text
        for row in markup.inline_keyboard
        for button in row
    )
    assert "Заявки арендаторов" in text


@pytest.mark.asyncio
async def test_approved_tenant_is_linked_to_owner(session):
    owner = User(
        telegram_id=20,
        apartment="20",
        role="resident",
        resident_subrole="owner",
        is_approved=True,
    )
    tenant = User(
        telegram_id=21,
        apartment="20",
        role="resident",
        resident_subrole="tenant",
        is_approved=True,
        approved_by_owner=owner,
    )
    session.add_all([owner, tenant])
    await session.commit()

    result = await session.execute(select(User).where(User.telegram_id == 21))
    saved_tenant = result.scalar_one()
    assert saved_tenant.approved_by_owner_id == owner.id
