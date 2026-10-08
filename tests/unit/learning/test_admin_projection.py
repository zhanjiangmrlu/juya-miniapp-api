from datetime import date

import pytest


@pytest.mark.asyncio
async def test_learning_overview_deduplicates_open_scenes_and_learning_days() -> None:
    # 功能:验证学习投影对开放场景完成与学习日期去重
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    from juya_miniapp_api.modules.learning.admin_projection import (
        InMemoryLearningOverviewRepository,
    )

    repository = InMemoryLearningOverviewRepository()
    repository.open_scene_completion_events.extend(
        [
            ("user-1", "scene-1"),
            ("user-1", "scene-1"),
            ("user-1", "scene-2"),
            ("user-2", "scene-3"),
        ]
    )
    repository.checkins.extend(
        [
            ("user-1", date(2026, 9, 27)),
            ("user-1", date(2026, 9, 27)),
            ("user-1", date(2026, 9, 28)),
        ]
    )
    repository.favorite_entries.extend(
        [
            ("user-1", "favorite-1"),
            ("user-1", "favorite-2"),
            ("user-2", "favorite-3"),
        ]
    )

    overview = await repository.get("user-1")

    assert overview.open_scene_completed_count == 2
    assert overview.learning_days == 2
    assert overview.favorite_count == 2


@pytest.mark.asyncio
async def test_learning_overview_returns_zero_counts_for_unknown_user() -> None:
    # 功能:验证未知用户的学习投影返回零数量
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    from juya_miniapp_api.modules.learning.admin_projection import (
        InMemoryLearningOverviewRepository,
    )

    overview = await InMemoryLearningOverviewRepository().get("missing-user")

    assert overview.open_scene_completed_count == 0
    assert overview.learning_days == 0
    assert overview.favorite_count == 0
