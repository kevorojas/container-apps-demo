"""
api — Backend interno (JSON, sin UI, sin ingress).

Orquesta la lógica de negocio para el `web`:
  - Subir documento: recibe el archivo del `web` y lo delega al `processor`
    (que valida, guarda en storage y registra en el catálogo).
  - Listar: consulta el `catalog`.

El `web` NUNCA toca el storage ni el catálogo directamente: siempre pasa
por acá. Este servicio no está expuesto al usuario; sólo lo alcanza el
`web` dentro de la red interna.
"""

import os

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile

PROCESSOR_URL = os.getenv("PROCESSOR_URL", "http://processor:8000")
CATALOG_URL = os.getenv("CATALOG_URL", "http://catalog:8000")

app = FastAPI(title="DocSafe · api", docs_url="/docs")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "api"}


@app.post("/documents", status_code=201)
async def upload_document(file: UploadFile = File(...)) -> dict:
    """Recibe un documento del `web` y lo delega al `processor`."""
    content = await file.read()

    files = {
        "file": (file.filename, content, file.content_type or "application/octet-stream"),
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{PROCESSOR_URL}/process", files=files)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"El processor no respondió: {exc}")

    if resp.status_code >= 400:
        # Propaga el error del processor (ej. tipo no permitido, tamaño).
        detail = "Error al procesar el documento."
        try:
            detail = resp.json().get("detail", detail)
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(status_code=resp.status_code, detail=detail)

    return resp.json()


@app.get("/documents")
async def list_documents() -> list[dict]:
    """Devuelve el catálogo consultando al `catalog`."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{CATALOG_URL}/documents")
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"El catálogo no respondió: {exc}")

    return resp.json()
