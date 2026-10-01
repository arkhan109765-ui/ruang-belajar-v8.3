FROM python:3.13-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY .env.example ./

RUN mkdir -p /app/data/pdf

CMD sh -c 'uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}'
