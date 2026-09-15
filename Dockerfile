FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5000 \
    OPEN_BROWSER=0 \
    RENEWALTRACKER_DATA_DIR=/data \
    VAPID_SUBJECT=mailto:renewaltracker@localhost

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY renewaltracker ./renewaltracker
COPY migrations ./migrations
COPY run.py .

RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown -R app:app /data /app
USER app

VOLUME ["/data"]
EXPOSE 5000

HEALTHCHECK --interval=60s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=4).status == 200 else 1)"

CMD ["python", "run.py", "--no-browser"]
