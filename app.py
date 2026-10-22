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
import uuid
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
import pymupdf4llm.helpers.document_layout as _layout  # noqa: E402

PORT = 8765

# Fremdrift: pymupdf4llm går gjennom sidene med en tekstbasert fremdriftsteller.
# Vi bytter den ut med en som melder hver side til jobben som kjører i samme tråd.
_progress = threading.local()


class _PageProgress:
    def __init__(self, items):
        self._items = list(items)
        # Bare første gjennomgang (sidetolkingen) tar tid; den neste er tekstgenereringen
        self._report = getattr(_progress, "report", None)
        _progress.report = None

    def __iter__(self):
        total = len(self._items)
        for i, item in enumerate(self._items):
            if self._report:
                self._report(i, total)
            yield item
        if self._report:
            self._report(total, total)


_layout.ProgressBar = _PageProgress
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


def convert(pdf_bytes: bytes, force_ocr: bool = False, on_page=None) -> str:
    # OCR kjøres automatisk på sider uten tekstlag; force_ocr tolker alle sider på nytt.
    # on_page(ferdige, totalt) kalles per side for dokumenter på fem sider eller mer.
    _progress.report = on_page
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            return pymupdf4llm.to_markdown(
                doc,
                show_progress=on_page is not None,
                ocr_language="nor+eng",
                ocr_dpi=300,
                force_ocr=force_ocr,
            )
    finally:
        _progress.report = None


class Job:
    def __init__(self, name: str, pdf_bytes: bytes, force_ocr: bool):
        self.id = uuid.uuid4().hex
        self.name, self.pdf_bytes, self.force_ocr = name, pdf_bytes, force_ocr
        self.done, self.pages = 0, 0
        self.finished = False
        self.result = {}

    def run(self):
        try:
            with pymupdf.open(stream=self.pdf_bytes, filetype="pdf") as doc:
                self.pages = doc.page_count
            md = convert(self.pdf_bytes, self.force_ocr, self._on_page)
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            out = unique_path(safe_stem(self.name))
            out.write_text(md, encoding="utf-8")
            self.result = {"file": out.name, "markdown": md}
        except Exception as e:
            self.result = {"error": f"Klarte ikke å lese PDF-en: {e}"}
        self.pdf_bytes = b""
        self.finished = True

    def _on_page(self, done, total):
        self.done, self.pages = done, total

    def status(self):
        return {"done": self.done, "pages": self.pages, "finished": self.finished, **self.result}


JOBS = {}


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
        url = urlparse(self.path)
        if url.path == "/":
            page = PAGE.replace("{{OUT_DIR}}", str(OUT_DIR).replace(str(Path.home()), "~"))
            self._send(200, page, "text/html; charset=utf-8")
        elif url.path == "/status":
            job = JOBS.get(parse_qs(url.query).get("id", [""])[0])
            if not job:
                return self._send(404, json.dumps({"error": "Ukjent jobb"}))
            status = job.status()
            if job.finished:
                JOBS.pop(job.id, None)
            self._send(200, json.dumps(status, ensure_ascii=False))
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
        # Konverteringen går i bakgrunnen; nettsiden henter fremdrift fra /status
        job = Job(name, self._body(), force_ocr)
        JOBS[job.id] = job
        threading.Thread(target=job.run, daemon=True).start()
        self._send(200, json.dumps({"id": job.id}))


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
  .progress { height: 4px; margin: 0 14px 12px; border-radius: 2px; background: var(--line); overflow: hidden; }
  .progress .fill { height: 100%; width: 0; background: var(--accent); transition: width .3s; }
  .progress.busy .fill { width: 30%; animation: slide 1.2s ease-in-out infinite; }
  @keyframes slide { from { transform: translateX(-100%); } to { transform: translateX(340%); } }
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

// Filene konverteres én om gangen, så fremdriften for hver fil gir mening
let queue = Promise.resolve();
function handle(files) {
  let prev = null;  // nye filer øverst, men i samme rekkefølge som de ble sluppet
  for (const f of files) {
    if (!f.name.toLowerCase().endsWith('.pdf')) continue;
    const li = document.createElement('li');
    li.innerHTML = '<div class="row"><span class="name"></span><span class="status">I kø</span></div><div class="progress"><div class="fill"></div></div><pre></pre>';
    li.querySelector('.name').textContent = f.name;
    prev ? prev.after(li) : list.prepend(li);
    prev = li;
    const ocr = document.getElementById('force').checked ? '1' : '0';
    queue = queue.then(() => convert(f, li, ocr));
  }
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function convert(f, li, ocr) {
  const status = li.querySelector('.status'), bar = li.querySelector('.progress'), fill = bar.querySelector('.fill');
  status.textContent = 'Starter …';
  bar.classList.add('busy');
  try {
    const res = await fetch('/convert?ocr=' + ocr + '&name=' + encodeURIComponent(f.name), { method: 'POST', body: f });
    const { id } = await res.json();
    let data;
    while (true) {
      await sleep(300);
      data = await (await fetch('/status?id=' + id)).json();
      if (data.finished) break;
      // Dokumenter under fem sider melder ikke fremdrift; da vises en løpende stripe
      if (data.done > 0) {
        const pct = Math.round(100 * data.done / data.pages);
        bar.classList.remove('busy');
        fill.style.width = pct + '%';
        status.textContent = pct + ' % · side ' + data.done + ' av ' + data.pages;
      } else {
        status.textContent = data.pages ? 'Konverterer ' + data.pages + (data.pages === 1 ? ' side …' : ' sider …') : 'Konverterer …';
      }
    }
    if (data.error) throw new Error(data.error);
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
  } finally {
    bar.remove();
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
