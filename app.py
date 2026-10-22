"""PDF til Markdown – enkel lokal konverteringsapp.

Kjøres på to måter:
  python app.py            starter en lokal webserver og åpner nettleseren
  python app.py --window   viser appen i et eget Mac-vindu (brukes i .app-pakken)

Konverteringen gjøres med pymupdf4llm. Sider uten tekstlag leses med Tesseract-OCR,
som er innebygd i PyMuPDF og bare trenger språkdatafiler (tessdata).
"""

import json
import multiprocessing
import os
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

APP_NAME = "PDF til Markdown"
FROZEN = getattr(sys, "frozen", False)
HERE = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent


def find_tessdata():
    candidates = [
        HERE / "tessdata",  # i .app-pakken, eller lastet ned av build.sh
        Path.home() / ".venvs" / "pdf_converter" / "tessdata",  # oppsett fra start-skriptet
    ]
    for path in candidates:
        if (path / "nor.traineddata").is_file():
            return path
    return None


TESSDATA = find_tessdata()
if TESSDATA:
    os.environ.setdefault("TESSDATA_PREFIX", str(TESSDATA))

import pymupdf  # noqa: E402  (må importeres etter at TESSDATA_PREFIX er satt)
import pymupdf4llm  # noqa: E402

PORT = 8765
# .app-en kan ikke skrive inni seg selv, så den lagrer i Dokumenter
OUT_DIR = Path.home() / "Documents" / APP_NAME if FROZEN else HERE / "Markdown"


def safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^\w\-. æøåÆØÅ]", "_", stem).strip() or "dokument"
    return stem


def unique_path(stem: str) -> Path:
    path = OUT_DIR / f"{stem}.md"
    n = 2
    while path.exists():
        path = OUT_DIR / f"{stem} ({n}).md"
        n += 1
    return path


def convert(pdf_bytes: bytes, force_ocr: bool = False) -> str:
    # OCR kjøres automatisk på sider uten tekstlag; force_ocr tolker alle sider på nytt
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        return pymupdf4llm.to_markdown(
            doc,
            show_progress=False,
            ocr_language="nor+eng",
            ocr_dpi=300,
            force_ocr=force_ocr,
        )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def do_GET(self):
        if urlparse(self.path).path == "/":
            page = PAGE.replace("{{OUT_DIR}}", str(OUT_DIR).replace(str(Path.home()), "~"))
            self._send(200, page, "text/html; charset=utf-8")
        else:
            self._send(404, "{}")

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/open-folder":
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            subprocess.run(["open", str(OUT_DIR)])
            return self._send(200, "{}")
        if url.path == "/copy":
            # Utklippstavla via pbcopy virker både i nettleser og i app-vinduet
            subprocess.run(["pbcopy"], input=self._body(), env={**os.environ, "LANG": "en_US.UTF-8"})
            return self._send(200, "{}")
        if url.path != "/convert":
            return self._send(404, "{}")
        query = parse_qs(url.query)
        name = unquote(query.get("name", ["dokument.pdf"])[0])
        force_ocr = query.get("ocr", ["0"])[0] == "1"
        pdf_bytes = self._body()
        try:
            md = convert(pdf_bytes, force_ocr)
        except Exception as e:
            return self._send(400, json.dumps({"error": f"Klarte ikke å lese PDF-en: {e}"}))
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = unique_path(safe_stem(name))
        out.write_text(md, encoding="utf-8")
        self._send(200, json.dumps({"file": out.name, "markdown": md}, ensure_ascii=False))


PAGE = r"""<!doctype html>
<html lang="no">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PDF til Markdown</title>
<style>
  :root { --bg:#f6f5f2; --card:#fff; --ink:#1d1d1f; --muted:#6e6e73; --line:#e3e1dc; --accent:#2f5d8a; --ok:#2e7d4f; --err:#b3261e; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#161617; --card:#1f1f21; --ink:#f2f2f2; --muted:#9a9aa0; --line:#333336; --accent:#7fb0e0; --ok:#6cc08f; --err:#f28b82; }
  }
  * { box-sizing: border-box; }
  body { margin:0; font:15px/1.5 -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif; background:var(--bg); color:var(--ink); }
  main { max-width: 900px; margin: 0 auto; padding: 32px 16px 64px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  p.sub { color: var(--muted); margin: 0 0 20px; }
  #drop { border: 2px dashed var(--line); border-radius: 14px; background: var(--card); padding: 44px 16px; text-align: center; cursor: pointer; transition: .15s; }
  #drop.over { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, var(--card)); }
  #drop strong { display:block; font-size: 17px; margin-bottom: 4px; }
  #drop span { color: var(--muted); }
  .bar { display:flex; justify-content:space-between; align-items:center; gap: 12px; flex-wrap: wrap; margin: 12px 0 0; }
  .bar label { color: var(--muted); cursor: pointer; }
  button { font: inherit; border: 1px solid var(--line); background: var(--card); color: var(--ink); border-radius: 8px; padding: 5px 12px; cursor: pointer; }
  button:hover { border-color: var(--accent); }
  ul { list-style: none; padding: 0; margin: 20px 0 0; }
  li { background: var(--card); border: 1px solid var(--line); border-radius: 12px; margin-bottom: 10px; overflow: hidden; }
  .row { display:flex; align-items:center; gap: 10px; padding: 12px 14px; }
  .name { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .status { font-size: 13px; color: var(--muted); }
  .status.ok { color: var(--ok); } .status.err { color: var(--err); }
  pre { margin:0; padding: 14px; border-top: 1px solid var(--line); max-height: 420px; overflow:auto; white-space: pre-wrap; word-break: break-word; font: 13px/1.5 ui-monospace, Menlo, monospace; display:none; }
  li.open pre { display:block; }
</style>
</head>
<body>
<main>
  <h1>PDF til Markdown</h1>
  <p class="sub">Alt skjer lokalt på maskinen. Skannede sider leses med tekstgjenkjenning (OCR). Ferdige filer lagres i {{OUT_DIR}}.</p>
  <div id="drop">
    <strong>Slipp PDF-er her</strong>
    <span>eller klikk for å velge filer</span>
    <input id="pick" type="file" accept="application/pdf,.pdf" multiple hidden>
  </div>
  <div class="bar">
    <label><input type="checkbox" id="force"> Kjør OCR på alle sider (for PDF-er med dårlig tekstlag)</label>
    <button id="folder">Åpne mappa</button>
  </div>
  <ul id="list"></ul>
</main>
<script>
const drop = document.getElementById('drop'), pick = document.getElementById('pick'), list = document.getElementById('list');
drop.onclick = () => pick.click();
pick.onchange = () => { handle(pick.files); pick.value = ''; };
// Hindre at en fil som slippes utenfor feltet åpnes i vinduet
['dragover','drop'].forEach(e => document.addEventListener(e, ev => ev.preventDefault()));
['dragenter','dragover'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add('over'); }));
['dragleave','drop'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', ev => handle(ev.dataTransfer.files));
document.getElementById('folder').onclick = () => fetch('/open-folder', {method:'POST'});

async function handle(files) {
  for (const f of files) {
    if (!f.name.toLowerCase().endsWith('.pdf')) continue;
    const li = document.createElement('li');
    li.innerHTML = '<div class="row"><span class="name"></span><span class="status">Konverterer …</span></div><pre></pre>';
    li.querySelector('.name').textContent = f.name;
    list.prepend(li);
    const status = li.querySelector('.status');
    try {
      const ocr = document.getElementById('force').checked ? '1' : '0';
      const res = await fetch('/convert?ocr=' + ocr + '&name=' + encodeURIComponent(f.name), { method: 'POST', body: f });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      status.textContent = 'Lagret som ' + data.file;
      status.className = 'status ok';
      li.querySelector('pre').textContent = data.markdown;
      const row = li.querySelector('.row');
      const show = btn('Vis', () => { li.classList.toggle('open'); show.textContent = li.classList.contains('open') ? 'Skjul' : 'Vis'; });
      const copy = btn('Kopier', async () => { await fetch('/copy', { method: 'POST', body: data.markdown }); copy.textContent = 'Kopiert'; setTimeout(() => copy.textContent = 'Kopier', 1500); });
      row.append(show, copy);
    } catch (e) {
      status.textContent = e.message || 'Noe gikk galt';
      status.className = 'status err';
    }
  }
}
function btn(label, fn) { const b = document.createElement('button'); b.textContent = label; b.onclick = fn; return b; }
</script>
</body>
</html>
"""


def run_browser():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"{APP_NAME} kjører på {url}  (Ctrl+C for å avslutte)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def run_window():
    import webview

    # Port 0 gir en ledig port, så flere kopier av appen ikke kolliderer
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    webview.create_window(APP_NAME, url, width=900, height=720, min_size=(520, 420))
    webview.start()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if FROZEN or "--window" in sys.argv:
        run_window()
    else:
        run_browser()
