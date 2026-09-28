#!/bin/sh
set -eu

case "${JUYA_PROCESS_TYPE:-api}" in
  api)
    exec uvicorn juya_miniapp_api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
    ;;
  worker)
    if [ "${JUYA_ENABLE_BEAT:-false}" = "true" ]; then
      exec celery -A juya_miniapp_api.infrastructure.tasks.celery_app:celery_app worker \
        --loglevel "${JUYA_LOG_LEVEL:-INFO}" -B --schedule /tmp/celerybeat-schedule
    fi
    exec celery -A juya_miniapp_api.infrastructure.tasks.celery_app:celery_app worker \
      --loglevel "${JUYA_LOG_LEVEL:-INFO}"
    ;;
  *)
    echo "Unsupported JUYA_PROCESS_TYPE: ${JUYA_PROCESS_TYPE}" >&2
    exit 64
    ;;
esac
