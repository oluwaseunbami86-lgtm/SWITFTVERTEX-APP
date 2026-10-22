#!/usr/bin/env bash
# Render build step. Fails loudly (set -e) - a failed migration must stop the deploy.
set -o errexit
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py lockdown_database
