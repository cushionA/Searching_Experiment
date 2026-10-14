FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=/tmp/hf
WORKDIR /app
COPY model/ /opt/model/
COPY synthetic-tool-logs.json /app/input.json
COPY run_poc.py /app/run_poc.py
RUN python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.7.1+cpu && \
    python -m pip install --index-url https://pypi.org/simple transformers==4.53.3 safetensors==0.5.3
USER 10001:10001
ENTRYPOINT ["python", "-B", "/app/run_poc.py"]
