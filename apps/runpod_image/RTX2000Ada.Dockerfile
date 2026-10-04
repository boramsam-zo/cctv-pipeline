FROM dahyeguk/cctv-runpod-ab@sha256:037355788382bd35081d798b4ff6f7131f7745735097058ba58f711af8477cb6

# Generated context replaces only the three executors and their full manifest.
# All model/source/environment bytes remain inherited from the frozen image.
COPY runtime/ /opt/cctv-image/runtime/
COPY image-manifest.json /opt/cctv-image/image-manifest.json
COPY service/ /opt/cctv-service/
WORKDIR /opt/cctv-service
ENV CCTV_ANALYSIS_DEPLOYMENT=rtx2000ada-20261003-v1
RUN /opt/cctv-image/.venv/bin/python -B /opt/cctv-image/runtime/image_runtime.py inspect
RUN /opt/cctv-image/.venv/bin/python -B /opt/cctv-image/runtime/image_runtime.py check-environment
RUN /opt/cctv-image/.venv/bin/python -B apps/runpod_image/verify_service_bundle.py
LABEL org.opencontainers.image.title="CCTV RTX 2000 Ada bounded service" \
      org.opencontainers.image.version="rtx2000ada-20261003-v1" \
      cctv.purpose="bounded-versioned-service; explicit-start-required"
# Inherit ENTRYPOINT and CMD ["pod"]. No service, budget, or inference auto-start.
