# Build only on the EC2 host after verifying this tag still has the recorded
# sha256:899b3c12f948784b821d02dcc27c38bd31952e961839a3014a2b4936d3ae82c5 ID.
# It is a local image ID, not a registry digest. Do not pull/recreate the base.
ARG BASE_IMAGE=cctv-be-team-rag:small-cpu-v1
FROM ${BASE_IMAGE}
COPY mlops_image_check.py /opt/cctv-mlops/image_check.py
COPY mlflow_requirements.txt /opt/cctv-mlops/requirements.txt
RUN python -B /opt/cctv-mlops/image_check.py snapshot --directory /opt/cctv-mlops
RUN python -m pip install --no-cache-dir --only-binary=:all: --retries 0 --timeout 60 \
    --constraint /opt/cctv-mlops/base-constraints.txt -r /opt/cctv-mlops/requirements.txt
RUN python -B /opt/cctv-mlops/image_check.py verify --directory /opt/cctv-mlops
RUN python -m pip check
# Compose supplies an explicit API/worker entrypoint after deployment checks.
# Running this image by itself only verifies package metadata and then exits.
ENTRYPOINT ["python", "-B", "/opt/cctv-mlops/image_check.py"]
CMD ["verify", "--directory", "/opt/cctv-mlops"]
