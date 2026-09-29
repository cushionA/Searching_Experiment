FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1
WORKDIR /app
RUN groupadd --gid 10001 lab && useradd --uid 10001 --gid lab --no-create-home lab && mkdir /data && chown lab:lab /data
COPY jse/__init__.py /app/jse/__init__.py
COPY jse/lab /app/jse/lab
COPY experiments /app/experiments

FROM runtime AS test
COPY tests /app/tests
RUN python -m unittest discover -s tests -p "test_lab*.py"
RUN python -c "from pathlib import Path; Path('/tmp/validated').write_text('offline tests passed\n')"

FROM runtime AS final
COPY --from=test /tmp/validated /app/validated.txt
USER 10001:10001
ENTRYPOINT ["python", "-m", "jse.lab"]
CMD ["--help"]
