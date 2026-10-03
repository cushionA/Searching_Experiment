# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS node-runtime
FROM scratch AS lightpanda-input

FROM python:3.12-slim-bookworm AS scenario-source
WORKDIR /app
COPY experiments/bot-diagnostics /app/experiments/bot-diagnostics
COPY scripts/with_proxy.py /app/scripts/with_proxy.py
COPY docs/proxy.md /app/docs/proxy.md
COPY --chmod=0555 experiments/bot-diagnostics/docker-entrypoint.sh /app/experiments/bot-diagnostics/docker-entrypoint.sh
COPY README.md AGENTS.md Dockerfile compose.yaml compose.bot-diagnostics-cloud.yaml .dockerignore /app/
COPY .agents/skills/bot-blocking-scenarios/SKILL.md /app/.agents/skills/bot-blocking-scenarios/SKILL.md
COPY docs/bot-diagnostics-2026-10-01.md docs/bot-diagnostics-evidence.md docs/oxibrowser-experiment.md docs/oxibrowser-selectors-20261001.json /app/docs/
COPY tests/test_lab_bot_diagnostics.py /app/tests/test_lab_bot_diagnostics.py
RUN chmod -R a+rX /app

FROM python:3.12-slim-bookworm AS bot-diagnostics
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 \
    BOT_DIAGNOSTICS_DEPS=/opt/bot-diagnostics \
    BOT_DIAGNOSTICS_STATE=/tmp/bot-diagnostics-state \
    BOT_DIAGNOSTICS_RUN_ROOT=/data \
    BOT_DIAGNOSTICS_CHROMIUM=/opt/bot-diagnostics/chromium
WORKDIR /app
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && groupadd --gid 10001 lab && useradd --uid 10001 --gid lab --create-home lab \
    && mkdir /data && chown lab:lab /data
COPY experiments/bot-diagnostics/package.json experiments/bot-diagnostics/package-lock.json experiments/bot-diagnostics/versions.json experiments/bot-diagnostics/setup.py /app/experiments/bot-diagnostics/
RUN --mount=type=secret,id=proxy_ca \
    --mount=type=secret,id=system_ca \
    --mount=type=bind,from=lightpanda-input,target=/run/lightpanda-input,ro \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    if [ -f /run/secrets/system_ca ]; then \
      export SSL_CERT_FILE=/run/secrets/system_ca; \
      printf 'Acquire::https::CaInfo "/run/secrets/system_ca";\n' > /tmp/bot-diagnostics-apt.conf; \
      export APT_CONFIG=/tmp/bot-diagnostics-apt.conf; \
    fi; \
    if [ -f /run/lightpanda-input/lightpanda ]; then \
      python3 -B experiments/bot-diagnostics/setup.py --with-browser --lightpanda-file /run/lightpanda-input/lightpanda; \
    else python3 -B experiments/bot-diagnostics/setup.py --with-browser; fi \
    && node /opt/bot-diagnostics/node_modules/playwright/cli.js install-deps chromium \
    && apt-get install -y --no-install-recommends libnss3-tools \
    && rm -rf /var/lib/apt/lists/* /app/.deps/npm-cache /tmp/bot-diagnostics-apt.conf \
    && chmod -R a+rX /opt/bot-diagnostics
COPY --from=scenario-source /app /app
RUN node experiments/bot-diagnostics/runner.test.mjs \
    && node experiments/bot-diagnostics/framework.test.mjs
USER 10001:10001
ENTRYPOINT ["/app/experiments/bot-diagnostics/docker-entrypoint.sh"]
CMD ["plan"]
