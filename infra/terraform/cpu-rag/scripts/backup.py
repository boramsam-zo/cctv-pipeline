"""Root-run logical backup. Credentials come from the EC2 instance role."""
from datetime import datetime, timezone
from pathlib import Path
import os
import subprocess
import tarfile
import boto3

def main(*, backup_dir=Path('/srv/cctv/backups'),
         bucket_file=Path('/opt/cctv-infra/backup-bucket'),
         compose_file=Path('/opt/cctv-stack/compose.yaml'),
         packages_file=Path('/srv/cctv/installed-packages.txt')):
    os.umask(0o077)
    bucket = bucket_file.read_text().strip()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    root = backup_dir / stamp
    root.mkdir(mode=0o700)
    for name, args in (
        ('cctv.dump', ['pg_dump', '-Fc', '-d', 'cctv']),
        ('roles.sql', ['pg_dumpall', '--globals-only']),
    ):
        with (root / name).open('xb') as out:
            subprocess.run(['docker', 'compose', '-f', str(compose_file), 'exec', '-T',
                            '--user', 'postgres', 'db', *args, '--no-password'], stdout=out, check=True)
    archive = root / 'backup.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name in ('cctv.dump', 'roles.sql'):
            tar.add(root / name, arcname=name)
        tar.add(compose_file, arcname='compose.yaml')
        tar.add(packages_file, arcname='installed-packages.txt')
    # Low-level single PutObject keeps permissions narrow. This demo has two chunks.
    if archive.stat().st_size >= 5 * 1024**3:
        raise RuntimeError('Backup exceeds single PUT limit; configure multipart backup before retrying')
    with archive.open('rb') as stream:
        boto3.client('s3', region_name='ap-northeast-2').put_object(
            Bucket=bucket, Key=f'cctv-db-backups/{stamp}/backup.tar.gz',
            Body=stream, ServerSideEncryption='AES256', ChecksumAlgorithm='SHA256')
    # Only files made by this successful run are removed; failed dumps remain for diagnosis.
    for name in ('cctv.dump', 'roles.sql', 'backup.tar.gz'):
        (root / name).unlink()
    root.rmdir()
    print(f'Backup uploaded: cctv-db-backups/{stamp}/backup.tar.gz')

if __name__ == '__main__':
    main()
