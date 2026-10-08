from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.infrastructure.tasks.celery_app import create_celery_app
from juya_miniapp_api.infrastructure.tasks.schedules import TASK_QUEUES


def test_celery_declares_all_domain_queues_and_account_schedules() -> None:
    # 功能:验证Celery声明全部业务队列与账号任务计划
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    app = create_celery_app(Settings(redis_url="redis://localhost:6379/9"))

    assert {queue.name for queue in app.conf.task_queues} == set(TASK_QUEUES)
    assert set(app.conf.beat_schedule) == {
        "execute-due-account-deletions",
        "dispatch-account-outbox",
    }
