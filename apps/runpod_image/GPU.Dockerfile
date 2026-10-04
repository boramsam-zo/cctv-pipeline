FROM dahyeguk/cctv-runpod-ab@sha256:037355788382bd35081d798b4ff6f7131f7745735097058ba58f711af8477cb6

# GPU model is detected at service startup, while code/models/packages stay pinned.
COPY runtime/ /opt/cctv-image/runtime/
COPY image-manifest.json /opt/cctv-image/image-manifest.json
COPY service/ /opt/cctv-service/
WORKDIR /opt/cctv-service
ENV CCTV_ANALYSIS_DEPLOYMENT=cuda-portable-20261003-v1
ENV CCTV_EXPECTED_GPU=auto
RUN /opt/cctv-image/.venv/bin/python -B /opt/cctv-image/runtime/image_runtime.py inspect
RUN /opt/cctv-image/.venv/bin/python -B /opt/cctv-image/runtime/image_runtime.py check-environment
RUN /opt/cctv-image/.venv/bin/python -B apps/runpod_image/verify_service_bundle.py
LABEL org.opencontainers.image.title="CCTV configurable CUDA service" \
      org.opencontainers.image.version="cuda-portable-20261003-v1" \
      cctv.purpose="bounded-versioned-service; explicit-start-required"
# Inherit ENTRYPOINT and CMD ["pod"]. Explicit API start sets a bounded test window.
