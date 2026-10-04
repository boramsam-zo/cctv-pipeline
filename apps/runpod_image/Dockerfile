FROM runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404@sha256:4d1721e62b56d345c83b4fd6090664be6daf9312caab5b2e76f23d8231941851

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    YOLO_OFFLINE=true HF_HUB_OFFLINE=1 WANDB_DISABLED=true
WORKDIR /opt/cctv-image
# Build only the generated allowlisted context, never the repository root.
COPY requirements.txt constraints.txt ./
RUN python3 -m venv --system-site-packages /opt/cctv-image/.venv \
    && /opt/cctv-image/.venv/bin/python -m pip install --no-cache-dir \
       -c constraints.txt -r requirements.txt
ENV PATH="/opt/cctv-image/.venv/bin:${PATH}"
COPY runtime/ ./runtime/
COPY sources/ ./sources/
COPY image-manifest.json build_check.py entrypoint.sh Dockerfile .dockerignore ./
RUN python runtime/image_runtime.py inspect \
    && python runtime/image_runtime.py check-environment \
    && python build_check.py \
    && chmod 755 entrypoint.sh
LABEL org.opencontainers.image.title="CCTV A/B reference runtime" \
      org.opencontainers.image.version="ab-p4-v1" \
      cctv.default_version="A" cctv.supported_versions="A,B" \
      cctv.purpose="bounded-offline-validation; service-integration-pending"
ENTRYPOINT ["/opt/cctv-image/entrypoint.sh"]
CMD ["pod"]
