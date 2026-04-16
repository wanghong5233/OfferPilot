FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY config /app/config

RUN pip install --upgrade pip && pip install .

EXPOSE 8010

CMD ["pulse", "start", "--host", "0.0.0.0", "--port", "8010"]
