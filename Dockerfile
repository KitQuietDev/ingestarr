FROM python:3.12-slim

LABEL maintainer="ingestarr"
LABEL description="Media intake and routing for *arr stacks"

RUN useradd -m -u 1000 ingestarr

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ingestarr/ ./ingestarr/

RUN mkdir -p /data/input/processed /data/state /data/logs /data/review \
    && chown -R ingestarr:ingestarr /data /app

USER ingestarr

STOPSIGNAL SIGTERM

ENTRYPOINT ["python", "-m", "ingestarr"]
CMD ["watch"]
