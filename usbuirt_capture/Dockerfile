FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends lirc && rm -rf /var/lib/apt/lists/*
COPY capture.py /capture.py
ENV PYTHONUNBUFFERED=1
CMD ["python", "/capture.py"]
