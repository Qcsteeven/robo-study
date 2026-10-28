# Робот ОП ИРНИТУ: веб-интерфейс + робот + OCR (Tesseract 5, rus/eng)
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config ./config
COPY robot_op ./robot_op
COPY demo_site ./demo_site

ENV ROBOT_APP_DATA_DIR=/data \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/openapi.json')"
CMD ["uvicorn", "robot_op.web.app:get_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
