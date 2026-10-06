FROM pr17-fixture-browser
RUN --mount=type=secret,id=proxy_ca,required=true,mode=0444 apt-get -o Acquire::https::CaInfo=/run/secrets/proxy_ca update && apt-get -o Acquire::https::CaInfo=/run/secrets/proxy_ca install -y --no-install-recommends libnss3-tools && rm -rf /var/lib/apt/lists/*
