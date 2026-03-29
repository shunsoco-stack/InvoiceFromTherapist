# Lightsail Container Service / 本番用
# ビルド: docker build -t invoice-salon:latest .
# ローカル: docker compose up --build

FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py .

# SQLite はデフォルトで /app/data（ボリュームマウント推奨）
RUN mkdir -p /app/data

EXPOSE 5000

# SQLite は同時書き込みに弱いため workers=1。PORT は Lightsail が渡す場合に対応。
CMD ["sh", "-c", "exec gunicorn -b 0.0.0.0:${PORT:-5000} --workers 1 --threads 8 --timeout 120 app.app:app"]
