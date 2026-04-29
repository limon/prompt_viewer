FROM python:3.13.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROMPT_VIEWER_PHOTO_ROOT=/data/photos \
    PROMPT_VIEWER_THUMB_ROOT=/data/thumbs \
    PROMPT_VIEWER_DB_PATH=/data/db/prompt_viewer.sqlite3

WORKDIR /app

COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt

COPY app.py comfy_png_summary.py ./
COPY static ./static

EXPOSE 8888

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8888"]
