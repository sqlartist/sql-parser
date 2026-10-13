FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY sql2tsql ./sql2tsql
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "sql2tsql.api:app", "--host", "0.0.0.0", "--port", "8000"]
