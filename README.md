<p align="center"><img src="packaging/icon.png" width="128" alt=""></p>

# PDF til Markdown

*English · [Norsk](README.nb.md)*

A small Mac app that converts PDFs to Markdown. Drop files into the window and one `.md` file per PDF is saved in `~/Documents/PDF til Markdown`. Everything runs locally on your Mac – nothing is uploaded anywhere.

Conversion is done by [pymupdf4llm](https://github.com/pymupdf/pymupdf4llm), which recognises headings, lists, bold and italic text, and tables. Scanned pages without a text layer are read with text recognition (Tesseract OCR) in English and Norwegian. If a PDF has a text layer that comes out garbled, you can tick a box to run OCR on every page. Each file gets a progress bar with percentage and page count, and multiple files are processed in a queue.

The app is built to handle large, image-heavy manuals. Screenshots are not OCR'd when the page already has text, repeated page headers, footers and chapter tabs in the margin are removed, and dot leaders in tables of contents don't turn into garbage characters. If you want the images, tick the box to include them. They are saved in a separate folder next to the Markdown file, and the text links to them.

The app is available in English and Norwegian. It follows your macOS language, and you can switch in the top right corner.

Requires a Mac with Apple Silicon and macOS 12 or later.

## Install

Download the latest `.dmg` from [Releases](https://github.com/foffster/pdf-til-markdown/releases/latest), open it and drag the app to your Applications folder. The app is signed and notarised by Apple.

## Run from source

You need Python 3.10 or later (for example `brew install python@3.12`).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py            # opens in your browser, saves to ./Markdown
.venv/bin/python app.py --window   # opens in its own window
```

OCR needs language data. Put `eng.traineddata` and `nor.traineddata` from [tessdata_best](https://github.com/tesseract-ocr/tessdata_best) in a folder called `tessdata` next to `app.py`. You don't need the Tesseract program itself – the OCR engine is built into PyMuPDF.

`Start PDF til Markdown.command` is a double-click shortcut. The first time, it sets everything up through Homebrew.

## Build the app

```bash
./build.sh
```

The script creates its own Python environment, downloads language data, builds `PDF til Markdown.app` with PyInstaller and packages it in a `.dmg`. By default the build happens in `~/Library/Caches/pdf-til-markdown-build`, so the project folder doesn't fill up with hundreds of megabytes. Change this with `BUILD_ROOT`.

Without further setup the app is only ad hoc signed. It works on the Mac that built it, but others will get a Gatekeeper warning.

### Signing and notarisation

This requires a paid Apple Developer membership and a one-time setup:

1. Create a **Developer ID Application** certificate at [developer.apple.com](https://developer.apple.com/account/resources/certificates/list) and install it in your keychain. Check that it's there with `security find-identity -v -p codesigning`.
2. Create an app-specific password at [account.apple.com](https://account.apple.com) and store it for notarisation:

   ```bash
   xcrun notarytool store-credentials pdf-til-markdown --apple-id YOUR@EMAIL --team-id TEAMID
   ```

Then build like this:

```bash
SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
NOTARY_PROFILE=pdf-til-markdown \
APP_VERSION=1.0.0 \
./build.sh
```

The script signs every library and the app itself with the hardened runtime, submits the dmg to Apple for notarisation, waits for the result and staples the ticket to both the app and the dmg.

## Project layout

| File | What |
|------|------|
| `app.py` | The whole app: local web server, conversion and user interface |
| `build.sh` | Builds, signs and notarises |
| `packaging/PDF til Markdown.spec` | PyInstaller configuration |
| `packaging/entitlements.plist` | Entitlements the app needs under the hardened runtime |
| `packaging/make_icon.py` | Draws the icon and creates `icon.icns` |

## Licence

The source code in this repository is licensed under [MIT](LICENSE).

The packaged app bundles components under other licences. PyMuPDF, PyMuPDF Layout and pymupdf4llm from Artifex are licensed under the GNU AGPL-3.0 (or a commercial licence from Artifex). This means the packaged app as a whole is distributed under the terms of the AGPL-3.0, and its source code must be available – as it is here. Other bundled components are pywebview (BSD-3-Clause), onnxruntime (MIT), NumPy (BSD-3-Clause) and Tesseract language data (Apache-2.0).
