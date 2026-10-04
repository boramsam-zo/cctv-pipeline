"""Explicit API embedding and transactional pgvector loading; no local AI models."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import urllib.request
import urllib.error

MODEL = 'gemini-embedding-001'
DIMENSIONS = 768
PROFILE = 'gemini-001-768-retrieval-v1'


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def validate_vector(values):
    if not isinstance(values, list) or len(values) != DIMENSIONS:
        raise ValueError('Embedding dimension mismatch')
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
        raise ValueError('Embedding contains invalid values')
    norm = math.sqrt(sum(v*v for v in values))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('Invalid embedding norm')
    return [v/norm for v in values]


def read_chunks(path, embedded=False):
    records, seen = [], set()
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        for key in ('chunk_id','document_id','content','embedding_input','source_url','effective_from'):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f'Missing {key}')
        if row['chunk_id'] in seen:
            raise ValueError('Duplicate chunk_id')
        seen.add(row['chunk_id'])
        if digest(row['content']) != row['content_sha256']:
            raise ValueError('Content hash mismatch')
        if embedded:
            if (row.get('embedding_model'), row.get('embedding_dimensions'), row.get('embedding_profile')) != (MODEL,DIMENSIONS,PROFILE):
                raise ValueError('Embedding profile mismatch')
            if row.get('embedding_input_sha256') != digest(row['embedding_input']):
                raise ValueError('Embedding input hash mismatch')
            row['embedding'] = validate_vector(row['embedding'])
        records.append(row)
    if not records:
        raise ValueError('Empty corpus')
    return records


def embed(text, task, *, key=None):
    key = key or os.environ.get('GEMINI_API_KEY')
    if not key:
        raise ValueError('Set GEMINI_API_KEY privately in the execution environment')
    body = {'model':f'models/{MODEL}', 'content':{'parts':[{'text':text}]},
            'taskType':task, 'outputDimensionality':DIMENSIONS}
    request = urllib.request.Request(
        f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:embedContent',
        data=json.dumps(body).encode(), headers={'Content-Type':'application/json','x-goog-api-key':key})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data=json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Embedding API HTTP {exc.code}; no automatic retry') from None
    return validate_vector(data['embedding']['values'])


def embed_file(source, target):
    rows = read_chunks(source)
    if Path(target).exists():
        raise FileExistsError('Use a new output path; originals are preserved')
    # Persist each completed chunk so a partial failure never masquerades as completion.
    with Path(target).open('x') as output:
        for row in rows:
            row = dict(row, embedding=embed(row['embedding_input'],'RETRIEVAL_DOCUMENT'),
                embedding_model=MODEL, embedding_dimensions=DIMENSIONS, embedding_profile=PROFILE,
                embedding_input_sha256=digest(row['embedding_input']),
                embedding_status='completed', database_loaded=False)
            output.write(json.dumps(row,ensure_ascii=False)+'\n')
            output.flush()
    Path(str(target)+'.complete.json').write_text(json.dumps({
        'source_sha256':hashlib.sha256(Path(source).read_bytes()).hexdigest(),
        'output_sha256':hashlib.sha256(Path(target).read_bytes()).hexdigest(),
        'chunks':len(rows),'profile':PROFILE},indent=2))
    return len(rows)


def connect():
    import psycopg
    dsn=os.environ.get('RAG_DATABASE_URL')
    if not dsn:
        raise ValueError('Set RAG_DATABASE_URL privately')
    return psycopg.connect(dsn,connect_timeout=10)


def load(path):
    rows=read_chunks(path,embedded=True)
    receipt=json.loads(Path(str(path)+'.complete.json').read_text())
    if receipt['chunks'] != len(rows) or receipt['output_sha256'] != hashlib.sha256(Path(path).read_bytes()).hexdigest():
        raise ValueError('Incomplete or changed embedding output')
    with connect() as conn:
        with conn.cursor() as cur:
            for row in rows:
                # Separate profiles and changed input versions; retries do not duplicate identical rows.
                cur.execute('''INSERT INTO cctv_rag.chunks
                    (chunk_id,embedding_profile,input_sha256,document_id,content,metadata,embedding)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::vector)
                    ON CONFLICT (chunk_id,embedding_profile,input_sha256) DO NOTHING''',
                    (row['chunk_id'],PROFILE,row['embedding_input_sha256'],row['document_id'],row['content'],
                     json.dumps({k:v for k,v in row.items() if k not in ('embedding','database_loaded')}),
                     json.dumps(row['embedding'])))
            for row in rows:
                cur.execute('''SELECT content, vector_dims(embedding) FROM cctv_rag.chunks
                    WHERE chunk_id=%s AND embedding_profile=%s AND input_sha256=%s''',
                    (row['chunk_id'],PROFILE,row['embedding_input_sha256']))
                if cur.fetchone() != (row['content'],DIMENSIONS):
                    raise ValueError('Stored row verification failed')
    return len(rows)


def search_vector(connection, vector, date):
    """Search an already persisted query vector; never calls an embedding API."""
    vector = validate_vector(vector)
    from psycopg.rows import dict_row
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT chunk_id, document_id, input_sha256,
            embedding_profile, content, metadata,
            embedding <=> %s::vector AS cosine_distance
            FROM cctv_rag.chunks WHERE embedding_profile=%s
            AND (metadata->>'effective_from')::date <= %s::date
            AND (metadata->>'effective_to' IS NULL
                 OR (metadata->>'effective_to')::date > %s::date)
            ORDER BY cosine_distance, chunk_id, input_sha256 LIMIT 5''',
            (json.dumps(vector), PROFILE, date, date))
        return [dict(row) for row in cur.fetchall()]


def search(text, date):
    vector = embed(text, 'RETRIEVAL_QUERY')
    with connect() as conn:
        rows = search_vector(conn, vector, date)
    return [dict(chunk_id=r['chunk_id'], content=r['content'],
                 source_url=r['metadata'].get('source_url'),
                 cosine_distance=r['cosine_distance']) for r in rows]


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ('check','embed','load'):
        p=sub.add_parser(command); p.add_argument('--input',type=Path,required=True)
        if command=='embed': p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('search');p.add_argument('--query',required=True);p.add_argument('--date',required=True)
    args=parser.parse_args()
    if args.command=='check': result={'chunks':len(read_chunks(args.input)),'api_calls':0,'db_writes':0}
    elif args.command=='embed': result={'embedded_chunks':embed_file(args.input,args.output),'database_loaded':False}
    elif args.command=='load': result={'verified_rows':load(args.input),'committed':True}
    else: result=search(args.query,args.date)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
