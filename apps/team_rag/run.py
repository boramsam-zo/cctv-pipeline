"""Local RAG runner: python run.py [input.json]."""
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL_REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'


def main():
    parser = argparse.ArgumentParser(description='입력 JSON → RAG 출력 JSON 및 사고 리포트')
    parser.add_argument('input', nargs='?', type=Path, default=ROOT / 'examples/demo_mountain_accident_fire_001.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs')
    parser.add_argument('--model-path', type=Path, help='이미 다운로드한 모델 폴더; 생략하면 첫 실행 시 다운로드')
    args = parser.parse_args()
    if args.model_path:
        os.environ['RAG_MODEL_PATH'] = str(args.model_path.resolve())
    from control_rag import retrieve, read, dump, markdown, build_index
    database = ROOT / 'data/index.sqlite'
    if not database.is_file():
        build_index(ROOT / 'data/chunks.jsonl', database, revision=MODEL_REVISION)
    result = retrieve(database, read(args.input), read(ROOT / 'contacts.json'))
    target = args.output.resolve()
    dump(target / (result['event_id'] + '.json'), result)
    md = target / (result['event_id'] + '.md')
    md.write_text(markdown(result), encoding='utf-8')
    print('JSON:', target / (result['event_id'] + '.json'))
    print('REPORT:', md)


if __name__ == '__main__':
    main()
