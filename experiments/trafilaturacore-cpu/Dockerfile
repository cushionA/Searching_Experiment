FROM python:3.12-slim
COPY libs /opt/libs
COPY bench.py fixtures.json /app/
ENV PYTHONPATH=/opt/libs PYTHONDONTWRITEBYTECODE=1
USER 65534:65534
WORKDIR /app
ENTRYPOINT ["python", "-B", "/app/bench.py"]
