#!/bin/sh
set -eu

# Render injects PORT for web services. Default for local/dev.
PORT="${PORT:-8000}"
export PORT

echo "PROCESS_TYPE=${PROCESS_TYPE:-web}"
echo "PORT=${PORT}"

run_migrate() {
  echo "Running migrations..."
  python manage.py migrate --noinput
}

run_collectstatic() {
  echo "Collecting static files..."
  python manage.py collectstatic --noinput
}

case "${PROCESS_TYPE:-web}" in
  web)
    run_migrate
    run_collectstatic
    exec gunicorn texagonbackend.wsgi:application \
      -b 0.0.0.0:${PORT} --workers 3 --threads 2 --timeout 60
    ;;

  worker-billing)
    exec celery -A texagonbackend worker -l info -Q billing --concurrency=4 --prefetch-multiplier=1
    ;;

  worker-email)
    exec celery -A texagonbackend worker -l info -Q email --concurrency=2 --prefetch-multiplier=1
    ;;

  cron-monthly-billing)
    # optional safety: ensure DB schema is up to date before running billing
    run_migrate
    exec python manage.py enqueue_subscription_invoices
    ;;
  cron-gamification)
    run_migrate
    exec python manage.py enqueue_gamification
    ;;

  *)
    echo "Unknown PROCESS_TYPE: ${PROCESS_TYPE}"
    exit 1
    ;;
esac
