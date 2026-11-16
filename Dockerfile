# One image = API + built web console. Built in the cloud by Render (no local Docker needed).
# The database is NOT built here: load it once with `python scripts/setup_db.py` (see DEPLOY.md).

# ---- stage 1: build the React console into ui/dist -------------------------
FROM node:22-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

# ---- stage 2: Python API that also serves ui/dist at / ---------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=ui /ui/dist ./ui/dist

EXPOSE 8000
# Render (and most hosts) pass the port in $PORT; 8000 when run elsewhere.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
