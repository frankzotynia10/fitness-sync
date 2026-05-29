FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY config.py .
COPY db.py .
COPY utils.py .
COPY hevy_api.py .
COPY tools/ tools/

CMD ["python", "app.py"]