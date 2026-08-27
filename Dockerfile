FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt ./
# Chromium wird nur als Fallback gestartet, wenn der HTTP-Bootstrap an einer
# Antibot-Challenge scheitert — muss aber vorher im Image liegen.
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY vinted_sniper ./vinted_sniper

RUN useradd --create-home --uid 1000 sniper \
 && mkdir -p /app/data \
 && chown -R sniper:sniper /app

USER sniper
VOLUME ["/app/data"]

CMD ["python", "-m", "vinted_sniper"]
