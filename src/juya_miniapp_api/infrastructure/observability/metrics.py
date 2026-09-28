from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

LOGIN_ATTEMPTS = Counter(
    "juya_miniapp_login_total",
    "Miniapp login outcomes",
    ("outcome",),
    registry=REGISTRY,
)
ADMIN_API_LATENCY = Histogram(
    "juya_miniapp_admin_api_seconds",
    "Admin API request latency",
    ("operation", "outcome"),
    registry=REGISTRY,
)
PROGRESS_FAILURES = Counter(
    "juya_miniapp_progress_failures_total",
    "Learning progress failures",
    ("code",),
    registry=REGISTRY,
)
MESSAGE_BACKLOG = Gauge(
    "juya_miniapp_message_backlog",
    "Pending message work",
    registry=REGISTRY,
)
DELETION_FAILURES = Counter(
    "juya_miniapp_deletion_failures_total",
    "Account deletion failures",
    ("stage",),
    registry=REGISTRY,
)
DB_POOL_CONNECTIONS = Gauge(
    "juya_miniapp_db_pool_connections",
    "Database pool connections",
    ("state",),
    registry=REGISTRY,
)
