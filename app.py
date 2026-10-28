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
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

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


# Sider med minst så mange tegn ekte tekst regnes som digitale og OCR-es ikke
MIN_TEXT_CHARS = 50


def make_ocr_function(force: bool):
    """Standard-OCR-en i pymupdf4llm tolker også bilder på sider som allerede har tekst,
    så skjermbilder i manualer blir til tegnsalat. Vi OCR-er bare sider uten tekstlag,
    med mindre brukeren har bedt om OCR på alle sider."""
    default = _layout.select_ocr_function()
    if default is None:
        return None

    def ocr(page, **kwargs):
        if not force:
            text = page.get_text().replace("�", "").strip()
            if len(text) >= MIN_TEXT_CHARS:
                return
        default(page, **kwargs)

    return ocr


def convert(pdf_bytes: bytes, force_ocr: bool = False, on_page=None, images_dir: Path = None) -> str:
    # on_page(ferdige, totalt) kalles per side for dokumenter på fem sider eller mer.
    # images_dir: lagre bildene der og lenke til dem; ellers utelates bildene.
    _progress.report = on_page
    # pymupdf4llm bytter ut mellomrom i hele lagringsstien til bildene, så de skrives
    # først til en midlertidig mappe uten mellomrom og flyttes etterpå
    tmp_images = Path(tempfile.mkdtemp(prefix="pdf2md_")) if images_dir else None
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            md = pymupdf4llm.to_markdown(
                doc,
                show_progress=on_page is not None,
                ocr_language="nor+eng",
                ocr_dpi=300,
                force_ocr=force_ocr,
                ocr_function=make_ocr_function(force_ocr),
                force_text=False,  # ikke ta med tekst som står inni bilder
                header=False,  # sidetopptekst og -bunntekst gjentas på hver side
                footer=False,
                write_images=images_dir is not None,
                image_path=str(tmp_images) if images_dir else "",
                filename="bilde",  # bildene heter bilde-<side>-<nr>.png
            )
        if images_dir:
            images_dir.mkdir(parents=True, exist_ok=True)
            for f in tmp_images.iterdir():
                shutil.move(str(f), images_dir / f.name)
            # Lenkene peker til den midlertidige mappa; gjør dem relative til .md-fila
            md = re.sub(r"\]\([^)\n]*?/(bilde-[^)/\n]+)\)",
                        lambda m: f"]({quote(images_dir.name)}/{m.group(1)})", md)
    finally:
        _progress.report = None
        if tmp_images:
            shutil.rmtree(tmp_images, ignore_errors=True)
    return clean_markdown(md)


def clean_markdown(md: str) -> str:
    # Prikkene i innholdsfortegnelser og punkttegn fra symbolfonter blir til U+FFFD
    md = re.sub(r"�{2,}", " … ", md)
    md = re.sub(r"^(\s*)�\s*", r"\1- ", md, flags=re.M)
    md = re.sub(r"(<br>)\s*�\s*", r"\1• ", md)
    md = md.replace("�", "•")

    # Korte linjer i store bokstaver som går igjen mange ganger (kapittelfaner i margen o.l.)
    lines = md.split("\n")

    def label(line):
        text = re.sub(r"^#+\s*|\*+", "", line).strip()
        return text if 0 < len(text) <= 25 and text.isupper() else None

    counts = {}
    for line in lines:
        key = label(line)
        if key:
            counts[key] = counts.get(key, 0) + 1
    lines = [line for line in lines if counts.get(label(line), 0) < 10]

    md = "\n".join(line.rstrip() for line in lines)
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


SETTINGS = Path.home() / "Library" / "Application Support" / APP_NAME / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_setting(key, value):
    settings = load_settings()
    settings[key] = value
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(settings), encoding="utf-8")


def ui_language() -> str:
    # Eget valg fra språkvelgeren går foran språket macOS står på
    saved = load_settings().get("lang")
    return saved if saved in ("nb", "en") else system_language()


def system_language() -> str:
    """Første foretrukne språk i macOS: «nb» for norsk, ellers «en»."""
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"],
                             capture_output=True, text=True, timeout=2).stdout
        # Utdata ser ut som ( "nb-NO", "en-US" ) – noen ganger uten anførselstegn
        first = re.search(r'\(\s*"?([A-Za-z]{2,3})', out).group(1).lower()
    except Exception:
        first = (os.environ.get("LANG") or "en")[:2].lower()
    return "nb" if first in ("nb", "no", "nn") else "en"


# Forhåndsvisningen i vinduet kuttes, så store manualer ikke gjør det tregt
PREVIEW_CHARS = 200_000


class Job:
    def __init__(self, name: str, pdf_bytes: bytes, force_ocr: bool, images: bool):
        self.id = uuid.uuid4().hex
        self.name, self.pdf_bytes, self.force_ocr, self.images = name, pdf_bytes, force_ocr, images
        self.done, self.pages = 0, 0
        self.finished = False
        self.result = {}

    def run(self):
        try:
            with pymupdf.open(stream=self.pdf_bytes, filetype="pdf") as doc:
                self.pages = doc.page_count
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            out = unique_path(safe_stem(self.name))
            images_dir = out.with_name(out.stem + " – bilder") if self.images else None
            md = convert(self.pdf_bytes, self.force_ocr, self._on_page, images_dir)
            out.write_text(md, encoding="utf-8")
            self.result = {"file": out.name, "markdown": md[:PREVIEW_CHARS],
                           "truncated": len(md) > PREVIEW_CHARS}
        except Exception as e:
            # Feilen oversettes i nettsiden; detaljen vises som den er
            self.result = {"error": "read_failed", "detail": str(e)}
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
            page = (PAGE.replace("{{OUT_DIR}}", str(OUT_DIR).replace(str(Path.home()), "~"))
                        .replace("{{LANG}}", ui_language()))
            self._send(200, page, "text/html; charset=utf-8")
        elif url.path == "/status":
            job = JOBS.get(parse_qs(url.query).get("id", [""])[0])
            if not job:
                return self._send(404, json.dumps({"error": "unknown_job", "finished": True}))
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
        if url.path == "/lang":
            lang = self._body().decode()
            if lang in ("nb", "en"):
                save_setting("lang", lang)
            return self._send(200, "{}")
        if url.path == "/copy":
            # Kopierer hele den lagrede fila (forhåndsvisningen kan være kuttet).
            # pbcopy virker både i nettleser og i app-vinduet.
            name = Path(unquote(parse_qs(url.query).get("file", [""])[0])).name
            path = OUT_DIR / name
            if name and path.is_file():
                subprocess.run(["pbcopy"], input=path.read_bytes(), env={**os.environ, "LANG": "en_US.UTF-8"})
            return self._send(200, "{}")
        if url.path != "/convert":
            return self._send(404, "{}")
        query = parse_qs(url.query)
        name = unquote(query.get("name", ["dokument.pdf"])[0])
        force_ocr = query.get("ocr", ["0"])[0] == "1"
        images = query.get("images", ["0"])[0] == "1"
        # Konverteringen går i bakgrunnen; nettsiden henter fremdrift fra /status
        job = Job(name, self._body(), force_ocr, images)
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
  header { display:flex; justify-content:space-between; align-items:baseline; gap: 12px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .lang { display:flex; gap: 2px; font-size: 13px; }
  .lang button { border: none; background: none; padding: 2px 6px; color: var(--muted); }
  .lang button.on { color: var(--ink); font-weight: 600; }
  p.sub { color: var(--muted); margin: 0 0 20px; }
  #drop { border: 2px dashed var(--line); border-radius: 14px; background: var(--card); padding: 44px 16px; text-align: center; cursor: pointer; transition: .15s; }
  #drop.over { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, var(--card)); }
  #drop strong { display:block; font-size: 17px; margin-bottom: 4px; }
  #drop span { color: var(--muted); }
  .bar { display:flex; justify-content:space-between; align-items:center; gap: 12px; flex-wrap: wrap; margin: 12px 0 0; }
  .bar label { color: var(--muted); cursor: pointer; }
  .opts { display:flex; flex-direction: column; gap: 4px; }
  .note { color: var(--muted); font: 12px -apple-system, sans-serif; padding: 8px 14px; border-top: 1px solid var(--line); display:none; }
  li.open .note.on { display:block; }
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
  <header>
    <h1 data-i18n="title">PDF til Markdown</h1>
    <div class="lang"><button data-lang="nb">Norsk</button><button data-lang="en">English</button></div>
  </header>
  <p class="sub" data-i18n="intro"></p>
  <div id="drop">
    <strong data-i18n="drop"></strong>
    <span data-i18n="pick"></span>
    <input id="pick" type="file" accept="application/pdf,.pdf" multiple hidden>
  </div>
  <div class="bar">
    <div class="opts">
      <label><input type="checkbox" id="force"> <span data-i18n="force"></span></label>
      <label><input type="checkbox" id="images"> <span data-i18n="images"></span></label>
    </div>
    <button id="folder" data-i18n="folder"></button>
  </div>
  <ul id="list"></ul>
</main>
<script>
const OUT_DIR = '{{OUT_DIR}}';
const TEXT = {
  nb: {
    title: 'PDF til Markdown',
    intro: 'Alt skjer lokalt på maskinen. Skannede sider leses med tekstgjenkjenning (OCR). Ferdige filer lagres i {dir}.',
    drop: 'Slipp PDF-er her', pick: 'eller klikk for å velge filer',
    force: 'Kjør OCR på alle sider (for PDF-er med dårlig tekstlag)', folder: 'Åpne mappa',
    images: 'Ta med bilder (lagres i en egen mappe ved siden av)',
    truncated: 'Forhåndsvisningen viser bare begynnelsen. Hele teksten ligger i fila, og «Kopier» tar med alt.',
    queued: 'I kø', starting: 'Starter …', converting: 'Konverterer …',
    convertingPages: 'Konverterer {n} sider …', convertingPage: 'Konverterer 1 side …',
    progress: '{pct} % · side {done} av {pages}', saved: 'Lagret som {file}',
    show: 'Vis', hide: 'Skjul', copy: 'Kopier', copied: 'Kopiert',
    read_failed: 'Klarte ikke å lese PDF-en: {detail}', unknown_job: 'Konverteringen forsvant. Prøv igjen.',
    failed: 'Noe gikk galt',
  },
  en: {
    title: 'PDF to Markdown',
    intro: 'Everything happens locally on your Mac. Scanned pages are read with text recognition (OCR). Converted files are saved in {dir}.',
    drop: 'Drop PDFs here', pick: 'or click to choose files',
    force: 'Run OCR on every page (for PDFs with a poor text layer)', folder: 'Open folder',
    images: 'Include images (saved in a separate folder next to it)',
    truncated: 'The preview only shows the beginning. The full text is in the file, and Copy includes all of it.',
    queued: 'Queued', starting: 'Starting …', converting: 'Converting …',
    convertingPages: 'Converting {n} pages …', convertingPage: 'Converting 1 page …',
    progress: '{pct}% · page {done} of {pages}', saved: 'Saved as {file}',
    show: 'Show', hide: 'Hide', copy: 'Copy', copied: 'Copied',
    read_failed: 'Could not read the PDF: {detail}', unknown_job: 'The conversion was lost. Please try again.',
    failed: 'Something went wrong',
  },
};

// Serveren bestemmer startspråket: eget valg (lagret i innstillingene) eller språket macOS står på
let lang = TEXT['{{LANG}}'] ? '{{LANG}}' : 'en';

function t(key, vars = {}) {
  return (TEXT[lang][key] || key).replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? '');
}

// Elementer med data-i18n (og eventuelt data-vars) tegnes på nytt når språket byttes
function setText(el, key, vars) {
  el.dataset.i18n = key;
  el.dataset.vars = JSON.stringify(vars || {});
  el.textContent = t(key, vars);
}

function applyLang() {
  document.documentElement.lang = lang;
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const vars = el.dataset.vars ? JSON.parse(el.dataset.vars) : {};
    if (el.dataset.i18n === 'intro') vars.dir = OUT_DIR;
    el.textContent = t(el.dataset.i18n, vars);
  });
  document.title = t('title');
  document.querySelectorAll('.lang button').forEach(b => b.classList.toggle('on', b.dataset.lang === lang));
}

document.querySelectorAll('.lang button').forEach(b => b.onclick = () => {
  lang = b.dataset.lang;
  fetch('/lang', { method: 'POST', body: lang });
  applyLang();
});
applyLang();

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
    li.innerHTML = '<div class="row"><span class="name"></span><span class="status"></span></div><div class="progress"><div class="fill"></div></div><pre></pre>';
    li.querySelector('.name').textContent = f.name;
    setText(li.querySelector('.status'), 'queued');
    prev ? prev.after(li) : list.prepend(li);
    prev = li;
    const opts = 'ocr=' + (document.getElementById('force').checked ? '1' : '0')
               + '&images=' + (document.getElementById('images').checked ? '1' : '0');
    queue = queue.then(() => convert(f, li, opts));
  }
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function convert(f, li, opts) {
  const status = li.querySelector('.status'), bar = li.querySelector('.progress'), fill = bar.querySelector('.fill');
  setText(status, 'starting');
  bar.classList.add('busy');
  try {
    const res = await fetch('/convert?' + opts + '&name=' + encodeURIComponent(f.name), { method: 'POST', body: f });
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
        setText(status, 'progress', { pct, done: data.done, pages: data.pages });
      } else if (data.pages) {
        setText(status, data.pages === 1 ? 'convertingPage' : 'convertingPages', { n: data.pages });
      } else {
        setText(status, 'converting');
      }
    }
    if (data.error) {
      setText(status, data.error, { detail: data.detail || '' });
      status.className = 'status err';
      return;
    }
    setText(status, 'saved', { file: data.file });
    status.className = 'status ok';
    li.querySelector('pre').textContent = data.markdown;
    if (data.truncated) {
      const note = document.createElement('div');
      note.className = 'note on';
      setText(note, 'truncated');
      li.querySelector('pre').after(note);
    }
    const row = li.querySelector('.row');
    const show = btn('show', () => { li.classList.toggle('open'); setText(show, li.classList.contains('open') ? 'hide' : 'show'); });
    const copy = btn('copy', async () => { await fetch('/copy?file=' + encodeURIComponent(data.file), { method: 'POST' }); setText(copy, 'copied'); setTimeout(() => setText(copy, 'copy'), 1500); });
    row.append(show, copy);
  } catch (e) {
    setText(status, 'failed');
    status.className = 'status err';
  } finally {
    bar.remove();
  }
}
function btn(key, fn) { const b = document.createElement('button'); setText(b, key); b.onclick = fn; return b; }
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
