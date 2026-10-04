"""PostgreSQL queue. A session advisory lock elects one dispatcher/collector.

Use a direct DB connection, not a transaction-pooling proxy. The private spool
must be shared by all queue processes on the same host. No remote IO in a SQL
transaction: submission intent is committed before the MP4 leaves the worker.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re

from .common import WorkerError


def connect():
    import psycopg
    from psycopg.rows import dict_row
    url = os.environ.get('BE_DATABASE_URL')
    options = dict(autocommit=True, row_factory=dict_row, connect_timeout=10)
    if url:
        return psycopg.connect(url, **options)
    password = Path('/run/secrets/db_app_password').read_text().strip()
    if not re.fullmatch('[a-f0-9]{64}', password):
        raise WorkerError('invalid_db_secret')
    return psycopg.connect(host='db', dbname='cctv', user='cctv_app', password=password, **options)


class Store:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def lock(self, kind):
        # 1: dispatch/enqueue/recovery. 2: collection. No expiry-based takeover
        # can run alongside a slow live owner; PostgreSQL releases on disconnect.
        key = {'dispatch': 1, 'collect': 2, 'flow': 3, 'admission': 4}[kind]
        acquired = self.connection.execute('SELECT pg_try_advisory_lock(734821, %s) AS ok', (key,)).fetchone()['ok']
        if not acquired:
            raise WorkerError('queue_worker_already_running')
        try:
            yield
        finally:
            if not self.connection.closed:
                self.connection.execute('SELECT pg_advisory_unlock(734821, %s)', (key,))

    def rows(self):
        return self.connection.execute('SELECT *, retry_after <= now() AS ready FROM cctv_jobs.queue ORDER BY sequence').fetchall()

    def by_key(self, key):
        return self.connection.execute('SELECT * FROM cctv_jobs.queue WHERE request_key=%s', (key,)).fetchone()

    def insert(self, key, directory_id, state):
        from psycopg.types.json import Jsonb
        return self.connection.execute('''INSERT INTO cctv_jobs.queue
          (worker_job_id, request_key, directory_id, state) VALUES (%s,%s,%s,%s) RETURNING *''',
          (state['worker_job_id'], key, directory_id, Jsonb(state))).fetchone()

    def write(self, row, state, *, attempt=False, delay=0, handoff=None):
        from psycopg.types.json import Jsonb
        self.connection.execute('''UPDATE cctv_jobs.queue SET state=%s,
          submit_attempts=submit_attempts+%s, retry_after=now()+(%s * interval '1 second'),
          handoff=COALESCE(%s,handoff), updated_at=now() WHERE worker_job_id=%s''',
          (Jsonb(state), int(attempt), delay, Jsonb(handoff) if handoff is not None else None, row['worker_job_id']))
