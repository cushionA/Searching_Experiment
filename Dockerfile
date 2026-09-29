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

FROM runtime AS crawl-tools
COPY requirements/crawl-tools.txt /app/requirements/crawl-tools.txt
COPY scripts/check_crawl_tools.py /app/scripts/check_crawl_tools.py
COPY scripts/check_cloud_environment.py /app/scripts/check_cloud_environment.py
RUN pip install --no-cache-dir -r /app/requirements/crawl-tools.txt
USER 10001:10001
ENTRYPOINT ["python", "/app/scripts/check_crawl_tools.py"]
CMD []

FROM crawl-tools AS browser-tools
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/patchright-browsers
COPY requirements/browser-tools.txt /app/requirements/browser-tools.txt
RUN pip install --no-cache-dir -r /app/requirements/browser-tools.txt \
    && python -m patchright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
USER 10001:10001
CMD ["--browser"]

FROM browser-tools AS adaptive-tools
USER root
ENV CRAWLEE_DISABLE_BROWSER_SANDBOX=true
COPY requirements/adaptive-tools.txt /app/requirements/adaptive-tools.txt
RUN pip install --no-cache-dir -r /app/requirements/adaptive-tools.txt \
    && python -m playwright install chromium \
    && python -B /app/scripts/check_cloud_environment.py --adaptive
USER 10001:10001
ENTRYPOINT ["python", "-m", "jse.lab"]
CMD ["--help"]

FROM runtime AS final
COPY --from=test /tmp/validated /app/validated.txt
USER 10001:10001
ENTRYPOINT ["python", "-m", "jse.lab"]
CMD ["--help"]
