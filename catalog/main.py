"""
catalog — Índice/metadata de documentos.

Servicio interno (sin UI, sin ingress público). Mantiene el catálogo de
documentos que el `processor` ya guardó en el blob storage. El `api` lo
consulta para armar el listado que ve el usuario.

Almacenamiento: SQLite sobre un archivo en un volumen. Mínimo a propósito
(la app demo existe para poner a prueba la infraestructura, no para ser un
producto). No hace falta Postgres.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Ruta del archivo SQLite. En docker-compose apunta a un volumen para persistir.
DB_PATH = os.getenv("CATALOG_DB_PATH", "/data/catalog.db")

app = FastAPI(title="DocSafe · catalog", docs_url="/docs")


def _init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL,
                sha256       TEXT    NOT NULL,
                size         INTEGER NOT NULL,
                content_type TEXT    NOT NULL,
                blob_name    TEXT    NOT NULL,
                created_at   TEXT    NOT NULL
            )
            """
        )
        conn.commit()


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


class DocumentIn(BaseModel):
    name: str
    sha256: str
    size: int
    content_type: str
    blob_name: str


class Document(DocumentIn):
    id: int
    created_at: str


@app.on_event("startup")
def startup() -> None:
    _init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "catalog"}


@app.post("/documents", response_model=Document, status_code=201)
def register_document(doc: DocumentIn) -> Document:
    """Registra la metadata de un documento ya guardado en storage."""
    created_at = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        cur = conn.execute(
            """
            INSERT INTO documents (name, sha256, size, content_type, blob_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (doc.name, doc.sha256, doc.size, doc.content_type, doc.blob_name, created_at),
        )
        conn.commit()
        new_id = cur.lastrowid
    return Document(id=new_id, created_at=created_at, **doc.model_dump())


@app.get("/documents", response_model=list[Document])
def list_documents() -> list[Document]:
    """Lista todos los documentos, del más nuevo al más viejo."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM documents ORDER BY id DESC"
        ).fetchall()
    return [Document(**dict(row)) for row in rows]


@app.get("/documents/{doc_id}", response_model=Document)
def get_document(doc_id: int) -> Document:
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return Document(**dict(row))
