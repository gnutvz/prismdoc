FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples

RUN pip install --no-cache-dir ".[api,llm]"

ENV PRISMDOC_CONFIG=/app/examples/retail/pipeline.yaml

EXPOSE 8000

CMD ["uvicorn", "prismdoc.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
