# FastAPI backend. NOT yet run by the team — see the note in compose.yaml.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# Build the schema + seed data, then serve the API.
CMD ["sh", "-c", "python scripts/setup_db.py && uvicorn api.main:app --host 0.0.0.0 --port 8000"]
