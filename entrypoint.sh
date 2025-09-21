#!/bin/sh
set -e

# Wait for Postgres (uses pg_isready available in the python:3.11-slim via libpq)
echo "⏳ Waiting for Postgres at $DB_HOST:$DB_PORT ..."
: "${DB_HOST:=db}"
: "${DB_PORT:=5432}"
until pg_isready -h "$DB_HOST" -p "$DB_PORT" -q; do
  >&2 echo "Postgres is unavailable - sleeping"
  sleep 2
done
echo "✅ Postgres is up!"


# Safe migrations
#python manage.py makemigrations user hr crm ecommerce expense finance inventory investor notification pricing purchase report sale setting syncqueue task voucher  --noinput
#python manage.py migrate --noinput

# Seed data (idempotent commands)
# python manage.py init_accounting 
# python manage.py init_vouchertypes 

# Collect static (optional, but good for nginx)
python manage.py collectstatic --noinput

# Run
exec gunicorn django_ledger_project.wsgi:application --bind 0.0.0.0:8000
