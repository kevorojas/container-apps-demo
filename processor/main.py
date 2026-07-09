"""
processor — Valida el documento y lo escribe en el blob storage.

Servicio interno (sin ingress). Flujo:
  1. Recibe el archivo desde el `api` (multipart).
  2. Valida: tamaño máximo, tipo de contenido permitido.
  3. Calcula el hash SHA-256.
  4. Escribe el blob en el storage (Azurite en local / managed identity en Azure).
  5. Avisa al `catalog` para que registre la metadata.

No expone la lógica de storage ni el catálogo al usuario: sólo lo alcanza
el `api` dentro de la red interna.
"""

import hashlib
import os
import uuid

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile

from storage import build_storage

CATALOG_URL = os.getenv("CATALOG_URL", "http://catalog:8000")

# Límite de tamaño (bytes). Default 10 MB.
MAX_SIZE_BYTES = int(os.getenv("MAX_SIZE_BYTES", str(10 * 1024 * 1024)))

# Tipos de contenido permitidos.
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "text/plain",
    "image/png",
    "image/jpeg",
}

app = FastAPI(title="DocSafe · processor", docs_url="/docs")

# La capa de storage se construye una vez al arrancar (lee STORAGE_MODE).
_storage = None


def get_storage():
    global _storage
    if _storage is None:
        _storage = build_storage()
    return _storage


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "processor"}


@app.post("/process", status_code=201)
async def process(file: UploadFile = File(...)) -> dict:
    content = await file.read()
    size = len(content)

    # --- Validaciones -----------------------------------------------------
    if size == 0:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")

    if size > MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"El archivo supera el máximo de {MAX_SIZE_BYTES // (1024 * 1024)} MB.",
        )

    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Tipo no permitido: {content_type}. "
                f"Permitidos: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}."
            ),
        )

    # --- Hash + escritura en storage -------------------------------------
    sha256 = hashlib.sha256(content).hexdigest()

    # Nombre de blob único para evitar colisiones (prefijo por hash corto).
    blob_name = f"{sha256[:12]}-{uuid.uuid4().hex[:8]}-{file.filename}"

    try:
        get_storage().put_blob(blob_name, content, content_type)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Error al escribir en storage: {exc}")

    # --- Registrar metadata en el catálogo -------------------------------
    metadata = {
        "name": file.filename,
        "sha256": sha256,
        "size": size,
        "content_type": content_type,
        "blob_name": blob_name,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{CATALOG_URL}/documents", json=metadata)
            resp.raise_for_status()
            doc = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Error al registrar en el catálogo: {exc}")

    return doc
