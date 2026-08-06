FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /collector

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY collector.py .
COPY web ./web

EXPOSE 8787

CMD ["python", "collector.py"]
