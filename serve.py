#!/usr/bin/env python3
"""Local dev server: builds the site, serves dist/, and rebuilds when anything in content/ or site/ changes.
Standard library only.

    python3 serve.py            http://localhost:8000
    python3 serve.py 9000       pick another port

Refresh the browser after saving. Files are served with no-cache headers so you always see the latest build.
Dev-only: a floating "Aa" font tester (tools/dev-fonts.js) is injected into pages. It is never part of dist/.
"""
import functools, http.server, pathlib, socketserver, sys, threading, time

import build

ROOT = pathlib.Path(__file__).resolve().parent
WATCH = [ROOT / "content", ROOT / "site", ROOT / "build.py"]


def snapshot():
    files = []
    for w in WATCH:
        files += [w] if w.is_file() else [p for p in w.rglob("*") if p.is_file()]
    return {str(p): p.stat().st_mtime_ns for p in files}


def rebuild():
    try:
        sys.argv = [sys.argv[0]]              # build.main() reads --check from argv; never pass it here
        build.main()
    except SystemExit:
        pass
    except Exception as e:                    # keep serving the last good build if a save leaves a file half-written
        print(f"build failed: {e}", file=sys.stderr)


def watch():
    seen = snapshot()
    while True:
        time.sleep(0.7)
        now = snapshot()
        if now != seen:
            seen = now
            print("change detected, rebuilding...")
            rebuild()


class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()
    def log_message(self, *a): pass           # quiet

    def _send(self, ctype, data):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        """Dev-only extras: serve the font tester script, and inject it into every HTML page (dist/ itself is never touched)."""
        path = self.path.split("?")[0].split("#")[0]
        if path == "/__dev/fonts.js":
            return self._send("application/javascript; charset=utf-8", (ROOT / "tools" / "dev-fonts.js").read_bytes())
        dist = pathlib.Path(self.directory).resolve()
        target = (dist / path.lstrip("/")).resolve()
        if path.endswith("/"): target = target / "index.html"
        try: target.relative_to(dist)
        except ValueError: return super().do_GET()            # outside dist: let the stock handler refuse it
        if target.suffix == ".html" and target.is_file():
            page = target.read_text(encoding="utf-8").replace("</body>", '<script src="/__dev/fonts.js"></script></body>')
            return self._send("text/html; charset=utf-8", page.encode("utf-8"))
        super().do_GET()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    rebuild()
    threading.Thread(target=watch, daemon=True).start()
    handler = functools.partial(NoCache, directory=str(ROOT / "dist"))   # path string, so dist/ being recreated is fine
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), handler) as srv:
        print(f"Serving http://localhost:{port}  (Ctrl-C to stop; watching content/ and site/)")
        try: srv.serve_forever()
        except KeyboardInterrupt: print()


if __name__ == "__main__":
    main()
