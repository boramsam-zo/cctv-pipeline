"""Compose wrapper for the existing CLI; no API/DB work until a command is selected."""
import os
from pathlib import Path
import re
from urllib.parse import quote

def configure(secret_dir=Path('/run/secrets')):
    password = (secret_dir / 'db_app_password').read_text().strip()
    if not re.fullmatch('[a-f0-9]{64}', password):
        raise ValueError('Invalid application DB password file')
    os.environ['RAG_DATABASE_URL'] = f'postgresql://cctv_app:{quote(password, safe="")}@db:5432/cctv'
    key = (secret_dir / 'gemini_api_key').read_text().strip()
    if key:
        os.environ['GEMINI_API_KEY'] = key

if __name__ == '__main__':
    configure()
    from index import main
    main()
