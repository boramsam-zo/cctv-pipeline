FROM dahyeguk/cctv-runpod-ab@sha256:037355788382bd35081d798b4ff6f7131f7745735097058ba58f711af8477cb6

# The base holds the already published dependencies and legacy model bytes.
# The allowlisted context contains source and metadata only. R2 is loaded on Pod.
COPY runtime/ /opt/cctv-image/runtime/
COPY image-manifest.json /opt/cctv-image/image-manifest.json
COPY service/ /opt/cctv-service/
WORKDIR /opt/cctv-service
ENV CCTV_ANALYSIS_DEPLOYMENT=mlops-20261004-v1
ENV CCTV_EXPECTED_GPU=auto
ENV PYTHONDONTWRITEBYTECODE=1
RUN /opt/cctv-image/.venv/bin/python -B /opt/cctv-image/runtime/image_runtime.py inspect
RUN /opt/cctv-image/.venv/bin/python -B /opt/cctv-image/runtime/image_runtime.py check-environment
RUN /opt/cctv-image/.venv/bin/python -B apps/runpod_image/verify_service_bundle.py
LABEL org.opencontainers.image.title="CCTV B/R2 CUDA service" \
      org.opencontainers.image.version="mlops-20261004-v1" \
      cctv.purpose="bounded-versioned-service; explicit-start-required"
# Inherit the management entrypoint and CMD ["pod"]; no automatic inference.
