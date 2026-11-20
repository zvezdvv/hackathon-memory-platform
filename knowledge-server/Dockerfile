FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV KNOWLEDGE_ROOT=/data/knowledge \
    RAG_API_URL=http://rag_api:8000 \
    PUBLIC_URL=http://localhost:8100

EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
