#!/bin/sh
set -eu

echo "PROCESS_TYPE=${PROCESS_TYPE:-web}"

case "${PROCESS_TYPE:-web}" in
  web)
    exec gunicorn texagonbackend.wsgi:application -b 0.0.0.0:${PORT:-8000} --workers 3 --threads 2 --timeout 60
    ;;
  worker-billing)
    exec celery -A texagonbackend worker -l info -Q billing --concurrency=4 --prefetch-multiplier=1
    ;;
  worker-email)
    exec celery -A texagonbackend worker -l info -Q email --concurrency=2 --prefetch-multiplier=1
    ;;
  cron-monthly-billing)
    exec python manage.py enqueue_subscription_invoices
    ;;
  *)
    echo "Unknown PROCESS_TYPE: ${PROCESS_TYPE}"
    exit 1
    ;;
esac
