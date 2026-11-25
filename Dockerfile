# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Serve by default; mount the route file and pass GITHUB_WEBHOOK_SECRET.
#   docker run --rm -p 8080:8080 -v "$PWD/routes.yaml:/config/routes.yaml" \
#     -e GITHUB_WEBHOOK_SECRET=... switchyard serve --config /config/routes.yaml --host 0.0.0.0
ENTRYPOINT ["switchyard"]
CMD ["--help"]
