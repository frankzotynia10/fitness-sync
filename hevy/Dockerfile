FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hevy_sync.py .

CMD ["python", "hevy_sync.py"]
