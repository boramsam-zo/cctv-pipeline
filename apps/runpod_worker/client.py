"""Small authenticated client; no automatic GPU job retries."""

import argparse
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", headers, fp)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["health", "submit", "status", "result", "log"])
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--token-file", type=Path, default=ROOT / ".api-token")
    parser.add_argument("--video", type=Path)
    parser.add_argument("--job")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    url = urllib.parse.urlsplit(args.url)
    if url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1")):
        parser.error("Use HTTPS for remote connections")
    if url.username or url.password or url.query or url.fragment:
        parser.error("Use a base URL without credentials or query parameters")
    if args.action in ("status", "result", "log") and (not args.job or len(args.job) != 32 or any(c not in '0123456789abcdef' for c in args.job)):
        parser.error("Provide --job with the returned job ID")
    token = args.token_file.read_text().strip()
    headers = {"Authorization": "Bearer " + token}
    path = "/health" if args.action == "health" else "/jobs"
    stream = None
    if args.action == "submit":
        if not args.video or not args.video.is_file():
            parser.error("Provide --video with an existing MP4")
        size = args.video.stat().st_size
        if not 0 < size <= 200 * 1024 * 1024:
            parser.error("Video must be between 1 byte and 200 MiB")
        headers.update({"Content-Type": "video/mp4", "Content-Length": str(size)})
        stream = args.video.open("rb")
    elif args.action != "health":
        path += "/" + args.job
        if args.action in ("result", "log"):
            path += "/" + args.action
    request = urllib.request.Request(args.url.rstrip("/") + path, data=stream, headers=headers,
                                     method="POST" if args.action == "submit" else "GET")
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=95) as response:
            if args.action in ("result", "log"):
                destination = args.output or Path(args.job + ("-results.zip" if args.action == "result" else "-worker.log"))
                with destination.open("xb") as target:
                    while block := response.read(1024 * 1024):
                        target.write(block)
                print(f"Saved: {destination}")
            else:
                print(json.dumps(json.load(response), indent=2))
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read(4096).decode(errors='replace')}")
        raise SystemExit(1)
    finally:
        if stream:
            stream.close()


if __name__ == "__main__":
    main()
