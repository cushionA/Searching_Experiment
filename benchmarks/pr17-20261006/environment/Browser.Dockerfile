FROM node:24-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20
RUN --mount=type=secret,id=proxy_ca,required=true,mode=0444 sed -i s,http://deb.debian.org,https://deb.debian.org,g /etc/apt/sources.list.d/debian.sources && apt-get -o Acquire::https::CaInfo=/run/secrets/proxy_ca update && apt-get -o Acquire::https::CaInfo=/run/secrets/proxy_ca install -y --no-install-recommends git firefox-esr xvfb xauth libgtk-3-0 libasound2 fonts-liberation && rm -rf /var/lib/apt/lists/*
ENV DISPLAY=:99
WORKDIR /workspace/pr17-validation
