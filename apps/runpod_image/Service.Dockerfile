FROM dahyeguk/cctv-runpod-ab@sha256:6cd6ee2e3f3e13a7f5d2bea5594db8206d4901f518ed3e73e6eaf4b4a2af55cb

# Build only the allowlisted context made by build_runpod_service_bundle.py.
# Keep the frozen models, GPU executor, manifest, and entrypoint at /opt/cctv-image.
WORKDIR /opt/cctv-service
COPY apps/ ./apps/
COPY service-manifest.json Dockerfile .dockerignore ./
RUN /opt/cctv-image/.venv/bin/python -B apps/runpod_image/verify_service_bundle.py
LABEL org.opencontainers.image.title="CCTV A/B service overlay" \
      org.opencontainers.image.version="ab-p6-service-v1" \
      cctv.purpose="bounded-versioned-service; explicit-start-required"
# Inherit P4 ENTRYPOINT and CMD ["pod"]: SSH/Jupyter remain available.
# The user starts apps/runpod_image/start-service.sh after setting finite limits.
