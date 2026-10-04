#!/bin/bash
set -euo pipefail
umask 027
bucket=$1
enable_s3_backup=${2:-false}
[[ "$enable_s3_backup" == true || "$enable_s3_backup" == false ]] || exit 2
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || { echo 'Ubuntu 24.04 required'; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl python3-boto3
install -m 0755 -d /etc/apt/keyrings
curl --fail --silent --show-error --location --proto '=https' \
  https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'APT'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
APT
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
python3 /opt/cctv-infra/prepare-host.py
docker compose -f /opt/cctv-stack/compose.yaml config --quiet
docker compose -f /opt/cctv-stack/compose.yaml up -d --wait --wait-timeout 180 db
{
  dpkg-query -W docker-ce docker-ce-cli containerd.io docker-compose-plugin python3-boto3
  docker compose version
  docker compose -f /opt/cctv-stack/compose.yaml exec -T --user postgres db \
    psql -X -d cctv -c 'SELECT version();' -c "SELECT extversion FROM pg_extension WHERE extname='vector';"
} > /srv/cctv/installed-packages.txt

# Application tables are deployed separately from apps/rag_index/schema.sql.
if [[ "$enable_s3_backup" == true ]]; then
install -d -m 0700 /srv/cctv/backups
printf '%s\n' "$bucket" > /opt/cctv-infra/backup-bucket
cat > /etc/systemd/system/cctv-db-backup.service <<'UNIT'
[Unit]
Description=CCTV PostgreSQL dump to private S3
After=docker.service network-online.target
Requires=docker.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/cctv-infra/backup.py
UMask=0077
UNIT
cat > /etc/systemd/system/cctv-db-backup.timer <<'UNIT'
[Unit]
Description=Daily CCTV database backup
[Timer]
OnCalendar=*-*-* 03:00:00 UTC
Persistent=true
RandomizedDelaySec=300
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now cctv-db-backup.timer
else
printf '%s\n' 'S3 automatic backup is disabled in the SSH deployment; no instance role configured.' > /srv/cctv/backup-status.txt
fi
touch /srv/cctv/bootstrap-complete
echo 'Compose PostgreSQL + pgvector ready; deploy schema and load the existing embedding file next.'
