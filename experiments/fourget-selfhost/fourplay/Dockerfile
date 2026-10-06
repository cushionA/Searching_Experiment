# syntax=docker/dockerfile:1
FROM node:26-bookworm-slim@sha256:3ffc19ea878019d9e9ae8971732ad4a03cda44f167107173174b60ed7c65bed3
RUN --mount=type=secret,id=proxy_ca,required=true \
    apt-get -o Acquire::https::CaInfo=/run/secrets/proxy_ca update \
    && apt-get -o Acquire::https::CaInfo=/run/secrets/proxy_ca install -y --no-install-recommends \
       firefox-esr xvfb xauth libnss3-tools ca-certificates fonts-liberation fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=secret,id=proxy_ca,required=true \
    NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca npm ci --ignore-scripts
COPY server.cjs navigation-gate.cjs entrypoint.sh ./
COPY policies.json /usr/lib/firefox-esr/distribution/policies.json
RUN chmod 755 /app/entrypoint.sh \
    && chmod 644 /app/server.cjs /app/package.json /app/package-lock.json \
       /usr/lib/firefox-esr/distribution/policies.json
USER node
ENV DISPLAY=:99 NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt
EXPOSE 3000
ENTRYPOINT ["/app/entrypoint.sh"]
