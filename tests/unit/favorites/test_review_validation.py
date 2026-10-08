from datetime import UTC, datetime, timedelta

import pytest

from juya_miniapp_api.modules.favorites.repository import InMemoryFavoriteRepository
from juya_miniapp_api.modules.favorites.service import FavoriteService
from juya_miniapp_api.shared.errors import AppError

NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("cards", [[], ["missing"], [1], ["x", "x"]])
async def test_invalid_cards_never_create_review_or_checkin(cards: list[object]) -> None:
    # 功能:验证无效卡片不创建复习或打卡记录
    # 参数:
    #     cards: 待核验归属或固定到复习会话的收藏标识集合
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryFavoriteRepository()
    service = FavoriteService(repository)
    with pytest.raises(AppError):
        await service.create_review("owner", cards, "create", NOW)
    assert repository.reviews == {}
    assert repository.checkins == set()


@pytest.mark.asyncio
async def test_review_pins_owned_cards_and_updates_only_their_review_time_once() -> None:
    # 功能:验证复习固定本人卡片且只更新这些卡片的复习时间一次
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryFavoriteRepository()
    service = FavoriteService(repository)
    cards = [
        await service.favorite("owner", "VOCABULARY", word, word, "s", word, "one", NOW)
        for word in ("one", "two")
    ]
    foreign = await service.favorite("other", "VOCABULARY", "foreign", "f", "s", "f", "one", NOW)
    with pytest.raises(AppError):
        await service.create_review("owner", [foreign.public_id], "bad", NOW)
    review = await service.create_review("owner", [cards[0].public_id], "create", NOW)
    assert review.card_ids == (cards[0].public_id,)
    with pytest.raises(AppError) as error:
        await service.create_review("owner", [cards[1].public_id], "create", NOW)
    assert error.value.code == "IDEMPOTENCY_KEY_CONFLICT"
    completed = NOW + timedelta(minutes=3)
    await service.complete_review("owner", review.id, "done", completed)
    await service.complete_review("owner", review.id, "again", completed + timedelta(days=1))
    assert (await repository.get("owner", cards[0].public_id)).last_reviewed_at == completed
    assert (await repository.get("owner", cards[1].public_id)).last_reviewed_at is None
    assert len(repository.checkins) == 1


@pytest.mark.asyncio
async def test_deleted_card_prevents_completion_and_checkin() -> None:
    # 功能:验证卡片被删除后拒绝复习完成与打卡
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryFavoriteRepository()
    service = FavoriteService(repository)
    card = await service.favorite("owner", "PHRASE", "hello", "h", "s", "h", "one", NOW)
    review = await service.create_review("owner", [card.public_id], "create", NOW)
    await service.delete("owner", card.public_id)
    with pytest.raises(AppError):
        await service.complete_review("owner", review.id, "done", NOW)
    assert repository.checkins == set()


@pytest.mark.asyncio
async def test_out_of_order_review_completions_do_not_move_last_reviewed_backwards() -> None:
    # 功能:验证乱序复习完成不使最近复习时间倒退
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryFavoriteRepository()
    service = FavoriteService(repository)
    card = await service.favorite("owner", "PHRASE", "hello", "h", "s", "h", "one", NOW)
    first = await service.create_review("owner", [card.public_id], "one", NOW)
    second = await service.create_review("owner", [card.public_id], "two", NOW)
    await service.complete_review("owner", second.id, "later", NOW + timedelta(minutes=3))
    await service.complete_review("owner", first.id, "earlier", NOW + timedelta(minutes=1))
    assert (await repository.get("owner", card.public_id)).last_reviewed_at == NOW + timedelta(
        minutes=3
    )
