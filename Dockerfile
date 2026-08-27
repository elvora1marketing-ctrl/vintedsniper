# Bewusst auf Bookworm (Debian 12) festgenagelt statt auf `python:3.11-slim`.
# Dieser Tag zeigt inzwischen auf Trixie (Debian 13), das Playwright 1.48 nicht
# kennt: es greift dann auf die Ubuntu-20.04-Paketliste zurück und will Pakete
# installieren, die es dort nicht mehr gibt (ttf-unifont,
# ttf-ubuntu-font-family) — der Build bricht mit apt-Fehler 100 ab.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt ./
# Chromium wird nur als Fallback gestartet, wenn der HTTP-Bootstrap an einer
# Antibot-Challenge scheitert — muss aber vorher im Image liegen.
#
# Zweiter Anlauf mit ausdrücklicher Paketliste, falls `--with-deps` an einer
# Distribution scheitert, die Playwright noch nicht kennt. Ohne den Rückfallweg
# reicht eine umbenannte Systembibliothek, um den ganzen Build zu kippen.
RUN pip install --no-cache-dir -r requirements.txt \
 && ( playwright install --with-deps chromium \
      || ( apt-get update \
        && apt-get install -y --no-install-recommends \
             fonts-liberation libasound2 libatk-bridge2.0-0 libatk1.0-0 \
             libatspi2.0-0 libcairo2 libcups2 libdbus-1-3 libdrm2 libexpat1 \
             libgbm1 libglib2.0-0 libnspr4 libnss3 libpango-1.0-0 libx11-6 \
             libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
             libxkbcommon0 libxrandr2 \
        && playwright install chromium ) ) \
 && rm -rf /var/lib/apt/lists/*

COPY vinted_sniper ./vinted_sniper

RUN useradd --create-home --uid 1000 sniper \
 && mkdir -p /app/data \
 && chown -R sniper:sniper /app

USER sniper
VOLUME ["/app/data"]

CMD ["python", "-m", "vinted_sniper"]
