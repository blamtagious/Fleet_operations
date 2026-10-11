FROM python:3.12-slim

# Chromium renders the quote PDFs (see app/lib/render.py)
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-liberation fonts-lato \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV FLEETQUOTE_BIND=0.0.0.0 \
    FLEETQUOTE_AUTH=1 \
    FLEETQUOTE_CHROME=/usr/bin/chromium \
    PYTHONUNBUFFERED=1

# Railway sets PORT. Also set FLEETQUOTE_SECRET, and point FLEETQUOTE_DATA and
# FLEETQUOTE_REF at the mounted volume (quotes/, upfit/, config/users.json and
# the two .xlsx files live there).
CMD ["python3", "app/server.py", "--no-open"]
