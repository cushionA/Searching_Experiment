FROM python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
COPY wheels /wheels
RUN pip install --no-index --find-links=/wheels onnxruntime==1.23.2 numpy==2.3.4 pillow==12.0.0 && rm -rf /wheels
COPY --chmod=644 *.onnx *.jpg probe.py /app/
USER 10001:10001
WORKDIR /app
ENTRYPOINT ["python", "-B", "/app/probe.py"]
