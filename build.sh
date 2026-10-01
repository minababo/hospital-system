#!/usr/bin/env bash
# Render build command. Migrations run here because the free tier has no pre-deploy step.
set -o errexit

echo "==> Installing dependencies"
pip install -r requirements.txt

echo "==> Running deployment checks"
python manage.py check --deploy --fail-level WARNING

echo "==> Collecting static files"
python manage.py collectstatic --noinput

echo "==> Applying migrations"
python manage.py migrate --noinput
