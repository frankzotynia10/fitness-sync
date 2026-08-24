FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY strava_sync.py .
COPY backfill_streams.py .
COPY backfill_segments.py .
COPY strava_replace_activity.py .
COPY services ./services

CMD ["python", "strava_sync.py"]
