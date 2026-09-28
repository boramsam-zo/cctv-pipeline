"""Split approved UTF-8 document sections, preserving source text and offsets."""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path


def chunks(documents, max_chars=1200):
    if not 100 <= max_chars <= 10000:
        raise ValueError('max_chars must be between 100 and 10000')
    rows, seen = [], set()
    for doc in documents:
        for field in ('document_id', 'title', 'source_url', 'effective_from', 'version'):
            if not isinstance(doc.get(field), str) or not doc[field].strip():
                raise ValueError(f'Missing {field}')
        if doc['document_id'] in seen:
            raise ValueError('Duplicate document_id; use a distinct ID for each version')
        seen.add(doc['document_id'])
        start_date = date.fromisoformat(doc['effective_from'])
        if doc.get('effective_to') and date.fromisoformat(doc['effective_to']) <= start_date:
            raise ValueError('effective_to must follow effective_from (exclusive end)')
        if not isinstance(doc.get('sections'), list) or not doc['sections']:
            raise ValueError('Provide at least one section')
        for section_index, section in enumerate(doc['sections']):
            if not isinstance(section.get('section'), str) or not section['section'].strip():
                raise ValueError('Missing section label')
            content = section.get('content')
            if not isinstance(content, str) or not content.strip():
                raise ValueError('Missing section content')
            start = 0
            while start < len(content):
                end = min(start + max_chars, len(content))
                if end < len(content):
                    # Prefer a paragraph/word boundary in the latter half of the slice.
                    boundary = max(content.rfind('\n', start + max_chars // 2, end),
                                   content.rfind(' ', start + max_chars // 2, end))
                    if boundary >= 0:
                        end = boundary + 1
                text = content[start:end]
                if text.strip():
                    identity = json.dumps([doc['document_id'], doc['version'], section_index,
                                           start, end, text], ensure_ascii=False)
                    row = {key: doc[key] for key in ('document_id', 'title', 'source_url',
                                                     'effective_from', 'version')}
                    row.update(chunk_id=hashlib.sha256(identity.encode('utf-8')).hexdigest(),
                        section=section['section'], section_index=section_index,
                        char_start=start, char_end=end, content=text,
                        content_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(),
                        embedding_input=f"{doc['title']}\n{section['section']}\n{text}",
                        embedding=None)
                    if doc.get('effective_to'):
                        row['effective_to'] = doc['effective_to']
                    if section.get('page') is not None:
                        row['page'] = section['page']
                    rows.append(row)
                start = end
    if not rows:
        raise ValueError('Empty corpus')
    return rows


def write_chunks(source, output, max_chars=1200):
    documents = [json.loads(line) for line in Path(source).read_text(encoding='utf-8').splitlines()
                 if line.strip()]
    rows = chunks(documents, max_chars)
    with Path(output).open('x', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-chars', type=int, default=1200)
    args = parser.parse_args()
    count = write_chunks(args.input, args.output, args.max_chars)
    print(json.dumps({'chunks': count, 'api_calls': 0, 'database_loaded': False}))


if __name__ == '__main__':
    main()
