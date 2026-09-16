# Build context is the REPO ROOT (see compose.yaml) so we can copy the console/ dir.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LENS_CONSOLE_DIR=/srv/console
WORKDIR /srv
COPY services/lens-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/lens-api/app ./app
COPY console ./console
EXPOSE 7710
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7710/health').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7710"]
