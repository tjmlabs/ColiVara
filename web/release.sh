#!/bin/bash

# Exit script if any command fails
set -e

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput --ignore css/style.css

# Apply database migrations
echo "Applying database migrations..."
python manage.py migrate --noinput


# start uvicorn server
echo "Starting server..."
uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --lifespan off --workers 7 --timeout-keep-alive 600 --limit-max-requests 1000

