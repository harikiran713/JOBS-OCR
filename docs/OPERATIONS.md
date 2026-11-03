# Operations: build, install, models, backup, troubleshooting

## Development

```powershell
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.lock.txt
.venv\Scripts\python.exe -m pytest

cd ..\desktop
npm ci
npx install-electron
npm run dev          # hot reload; backend is started from backend\.venv
npm run typecheck
npm test
```

Python 3.12 is required. PaddlePaddle and some wheels lag behind newer Python versions, and the lock file was resolved on 3.12. The backend can also be run on its own:

```powershell
$env:MMI_API_TOKEN="dev"; .venv\Scripts\python.exe -m mmi --port 8765 --data-dir .\devdata
```

> VS Code terminals export `ELECTRON_RUN_AS_NODE=1`, which turns Electron into plain Node. `npm run dev` and `npm start` go through `scripts/run.mjs`, which clears it.

## Production build

```powershell
cd desktop
npm run dist:win     # electron-vite build → PyInstaller backend → electron-builder NSIS (x64)
```

- `scripts/build-backend.mjs` runs `PyInstaller mmi-backend.spec`, which produces `backend/dist/mmi-backend/` (~380 MB, mostly ONNX Runtime and MuPDF). RapidOCR's ONNX models are bundled into it, so the installed application never downloads OCR models.
- electron-builder copies that folder to `resources/backend`, and the app starts `mmi-backend.exe` from there.
- The installer is per-user, lets the user choose the install folder, and **does not delete user data on uninstall**.

### Code signing (required)

On the build machine, Windows Smart App Control / Application Control blocked both the unsigned `mmi-backend.exe` and the unsigned NSIS uninstaller that is generated during the build, so the build stopped with `spawn UNKNOWN`. Distributing the application therefore requires:

1. An OV/EV Authenticode certificate, or Azure Trusted Signing.
2. Signing *both* the Electron executables and `mmi-backend.exe` together with all of its `.exe` and `.dll` files. Sign the backend before electron-builder packages it, then configure `win.signtoolOptions` (or `azureSignOptions`) in `desktop/package.json`.
3. A test install on a clean Windows 10 machine and a clean Windows 11 machine with Smart App Control **on**.

Until signing is in place, test packaged builds only on machines where Smart App Control is off.

## Upgrades

The data directory (`%APPDATA%\Marine Maintenance Intelligence\data`) is separate from the install folder, so installing a new version over an old one keeps every project. The database is copied to `data\backups\` before any schema migration runs. If the database is newer than the application, the application refuses to start and leaves the database unmodified.

## Local AI models (optional)

The application is fully usable with no AI model installed. To add one:

1. Install [Ollama](https://ollama.com), or run llama.cpp's `llama-server` (or LM Studio) on this computer.
2. Open **Settings → Local AI** and choose the provider and server URL. For Ollama, the **Download** buttons fetch a model through Ollama's own registry. The size of each model is shown before you download it; the download is the only network activity involved.
3. Set a chat model, plus an embedding model if you want semantic retrieval. Then press **Test connection**.

For llama.cpp, start the server yourself, for example `llama-server -m <model.gguf> --port 8080` and, for embeddings, a second instance started with `--embeddings`. Model files can live anywhere you choose; nothing is hard-coded.

The suggested models (Qwen3 4B/8B and Phi-4-mini for chat, nomic-embed-text and bge-m3 for embeddings, Qwen2.5-VL for vision) were picked for permissive licences and hardware fit. **None of them has been benchmarked on marine manuals with this application.** Evaluate before relying on one:

```powershell
.venv\Scripts\python.exe -m mmi.evaluation --data-dir <data dir> --project <project id> --truth truth.json --out report.json
```

The ground-truth format is documented at the top of `backend/mmi/evaluation.py`, and `tests/fixtures/ground_truth.json` is an example. CPU-only machines work, but LLM steps are slow; increase **Request timeout** if needed. Every model output is checked against source text: ungrounded jobs are discarded and ungrounded verifications are not accepted. Models never write description text — long and short descriptions are always copied word for word from the manual.

## Backup and restore

- **Backup:** close the application, then copy the entire data folder (**Settings → Data & privacy → Open folder**). It contains `mmi.sqlite3`, the managed copies of the manuals, caches and logs.
- **Restore:** close the application and replace the data folder with the backup.
- `cache\renders` can be deleted at any time; page images are regenerated when needed.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "The processing engine is not running" | Click **Open logs** (`data\logs\backend.log`). Check that antivirus or Application Control is not blocking `mmi-backend.exe` (see Code signing above). Then **Restart engine**. |
| Pages marked "OCR required but unavailable" | The OCR engine failed to load. Run **Settings → OCR → Test OCR** and check the log. |
| Pages marked "Low OCR confidence" | Raise the OCR DPI (250–300) and reprocess the page from the document viewer's page registry. |
| A document shows "Error – password protected" | Remove the password with the document owner's tools and import the file again. |
| Import fails with a path error | The data folder path is very long. Set `MMI_DATA_DIR` to a shorter location. |
| Processing pauses with "Waiting for free memory" | Close other applications, or lower **Minimum free memory** / **Parallel workers**. |
| AI status shows "unavailable" | Check that the inference server is running and the URL is correct. Non-local URLs need **Allow non-local endpoints**. |
