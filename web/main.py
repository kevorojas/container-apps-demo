"""
web — Frontend / BFF (el ÚNICO servicio con puerto expuesto al usuario).

Server-rendered con Jinja2 + HTMX. Sin build step, sin SPA. Dos pantallas
en una sola página:
  1. Subir un documento (form HTMX → POST /upload → llama al `api`).
  2. Listar el catálogo (tabla que se refresca vía HTMX tras subir).

Regla de oro: el `web` NUNCA llama al storage ni al catálogo directamente.
Siempre le pega al `api` por HTTP interno. En la arquitectura de Azure ésta
es la única "puerta pública" (detrás del Application Gateway); todo lo demás
es privado.
"""

import os

import httpx
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

API_URL = os.getenv("API_URL", "http://api:8000")

app = FastAPI(title="DocSafe · web")

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


templates.env.filters["human_size"] = _human_size


async def _fetch_documents() -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{API_URL}/documents")
        resp.raise_for_status()
        return resp.json()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "web"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    try:
        documents = await _fetch_documents()
    except httpx.HTTPError:
        documents = []
    return templates.TemplateResponse(
        "index.html", {"request": request, "documents": documents}
    )


@app.get("/documents", response_class=HTMLResponse)
async def documents_partial(request: Request):
    """Fragmento HTMX: sólo la tabla del catálogo."""
    try:
        documents = await _fetch_documents()
    except httpx.HTTPError:
        documents = []
    return templates.TemplateResponse(
        "_documents.html", {"request": request, "documents": documents}
    )


@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, file: UploadFile = File(...)):
    """
    Recibe el archivo del form HTMX, lo manda al `api` y devuelve un
    fragmento parcial: el listado actualizado + un banner de resultado.
    """
    content = await file.read()
    files = {
        "file": (file.filename, content, file.content_type or "application/octet-stream"),
    }

    error: str | None = None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{API_URL}/documents", files=files)
        if resp.status_code >= 400:
            try:
                error = resp.json().get("detail", "No se pudo subir el documento.")
            except Exception:  # noqa: BLE001
                error = "No se pudo subir el documento."
    except httpx.HTTPError:
        error = "El servicio no está disponible en este momento."

    try:
        documents = await _fetch_documents()
    except httpx.HTTPError:
        documents = []

    return templates.TemplateResponse(
        "_upload_result.html",
        {"request": request, "documents": documents, "error": error},
    )
