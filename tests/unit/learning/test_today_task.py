from juya_miniapp_api.modules.learning.domain import TodayTask
from juya_miniapp_api.modules.learning.service import select_today_task


def test_today_task_uses_fixed_priority_order() -> None:
    unfinished = [TodayTask("SCENE", "recent-unfinished")]
    new_scenes = [TodayTask("SCENE", "first-new")]
    reviews = [TodayTask("FAVORITE", f"card-{index}") for index in range(12)]
    history = [TodayTask("SCENE_REVIEW", "oldest-review")]

    assert select_today_task(unfinished, new_scenes, reviews, history) == unfinished[0]
    assert select_today_task([], new_scenes, reviews, history) == new_scenes[0]
    review_task = select_today_task([], [], reviews, history)
    assert review_task is not None
    assert review_task.kind == "FAVORITE_REVIEW"
    assert review_task.card_ids == tuple(f"card-{index}" for index in range(10))
    assert select_today_task([], [], [], history) == history[0]
    assert select_today_task([], [], [], []) is None
