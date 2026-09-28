from typing import Final

TASK_QUEUES: Final = (
    "miniapp.message",
    "miniapp.account",
    "miniapp.privacy",
    "miniapp.stats",
)

BEAT_SCHEDULE: Final = {
    "execute-due-account-deletions": {
        "task": "juya.accounts.execute_due",
        "schedule": 60.0,
        "options": {"queue": "miniapp.account"},
    },
    "dispatch-account-outbox": {
        "task": "juya.accounts.dispatch_outbox",
        "schedule": 15.0,
        "options": {"queue": "miniapp.account"},
    },
}
