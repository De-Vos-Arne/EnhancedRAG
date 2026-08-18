FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src

# Dependencies first, so code edits don't reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/

# data/, exports/ and results/ are bind-mounted by compose, so the archive
# and the built corpus stay on the host and survive a rebuild.
ENV RAG_HOST=0.0.0.0 RAG_PORT=5000 OLLAMA_URL=http://host.docker.internal:11434
EXPOSE 5000
CMD ["python", "scripts/serve.py"]
