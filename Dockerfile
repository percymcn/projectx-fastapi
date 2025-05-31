FROM python:3.11-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 5002
CMD ["uvicorn", "projectx_fastapi_threaded:app", "--host", "0.0.0.0", "--port", "5002"]