FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

WORKDIR /app

COPY backend/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

CMD ["sh", "-c", "exec gunicorn backend.api:application -c backend/gunicorn.conf.py --bind 0.0.0.0:${PORT:-8090}"]
