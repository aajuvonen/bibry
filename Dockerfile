FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./

RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY wsgi.py ./wsgi.py

RUN mkdir -p /app/bib /app/pdf

EXPOSE 5000

CMD ["gunicorn", "wsgi:app", "--workers", "4", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:5000"]
