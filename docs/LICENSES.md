# Third-party licences: review before distribution

This is an engineering summary, not legal advice. Check each project's current licence before you ship.

## Licences that need action

| Component | Licence | Implication |
|---|---|---|
| **PyMuPDF / MuPDF** (PDF text, tables, rendering) | **AGPL-3.0** or commercial licence from Artifex | Distributing the application to others requires either releasing the complete application source under AGPL-3.0, or buying a commercial PyMuPDF licence. Settle this before external distribution. `pdfplumber`/`pypdfium2` (Apache/BSD) could replace it, but table extraction and rendering would have to be re-validated. |
| Qwen2.5-VL-3B (optional vision model) | Qwen Research License | Not for commercial use; the app labels it this way and does not suggest it by default. |
| Llama-family models (if a user chooses one) | Llama Community License | Acceptable-use and attribution terms apply. |

## Permissive (attribution required in the installer or about box)

Electron (MIT), React (MIT), Vite (MIT), Tailwind CSS (MIT), Radix UI (MIT), lucide (ISC), TanStack Query/Table/Virtual (MIT), Zustand (MIT), React Hook Form (MIT), Zod (MIT), sonner (MIT), FastAPI (MIT), Starlette (BSD-3), Uvicorn (BSD-3), Pydantic (MIT), SQLAlchemy (MIT), openpyxl (MIT), NumPy (BSD-3), httpx (BSD-3), psutil (BSD-3), RapidOCR (Apache-2.0), PaddleOCR models (Apache-2.0), ONNX Runtime (MIT), Shapely (BSD-3), OpenCV (Apache-2.0, a RapidOCR dependency).

Suggested models with permissive licences: Qwen3 4B/8B (Apache-2.0), Phi-4-mini (MIT), nomic-embed-text (Apache-2.0), bge-m3 (MIT), Qwen2.5-VL-7B (Apache-2.0). The application never bundles models; users download them explicitly.

Generate the full list with versions for the release notes:

```powershell
cd desktop; npx license-checker --production --summary
cd ..\backend; .venv\Scripts\python.exe -m pip install pip-licenses; .venv\Scripts\pip-licenses --from=mixed
```
