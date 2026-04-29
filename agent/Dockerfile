FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY a2a_server.py ./

EXPOSE 8080

CMD ["uvicorn", "a2a_server:app", "--host", "0.0.0.0", "--port", "8080"]
