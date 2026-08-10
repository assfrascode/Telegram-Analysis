# syntax=docker/dockerfile:1.7

ARG PYTHON_BASE_IMAGE=python:slim

FROM ${PYTHON_BASE_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM ${PYTHON_BASE_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:$PATH \
    HOME=/home/collector \
    TELEGRAM_SESSION_PATH=/data/telegram-external.session

RUN groupadd --gid 10001 collector \
    && useradd --uid 10001 --gid collector --create-home --shell /usr/sbin/nologin collector \
    && install -d -o collector -g collector -m 0700 /data

WORKDIR /collector

COPY --from=builder /opt/venv /opt/venv
COPY --chown=collector:collector collector.py .
COPY --chown=collector:collector web ./web

USER 10001:10001

VOLUME ["/data"]
EXPOSE 8787

CMD ["python", "collector.py"]
