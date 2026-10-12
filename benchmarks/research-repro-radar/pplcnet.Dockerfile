FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
COPY wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels paddlepaddle==3.2.2 Pillow==11.3.0 && rm -rf /wheels
COPY pp_lcnet.py weights.pdparams labels.txt dog.jpg smoke.py /app/
RUN chmod -R a+rX /app
ENV OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
WORKDIR /app
CMD ["python", "smoke.py"]
