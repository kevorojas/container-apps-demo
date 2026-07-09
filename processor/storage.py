"""
Capa de storage — abstracción para escribir/leer blobs.

Objetivo de diseño: que el MISMO código funcione en local (contra Azurite,
el emulador de Azure Storage) y en producción (contra Azure Blob Storage
real, autenticando con managed identity, SIN connection strings).

Se usa el SDK real `azure-storage-blob` en ambos casos: así el código es
idéntico al de producción y sólo cambia cómo se construye el cliente
(endpoint + credencial). Eso se elige por variables de entorno:

  STORAGE_MODE = azurite | managed_identity   (default: azurite)

  - azurite            → BlobServiceClient.from_connection_string(...)
                         usando el connection string de desarrollo de Azurite.
  - managed_identity   → BlobServiceClient(account_url, credential=DefaultAzureCredential())
                         apuntando a STORAGE_ACCOUNT_URL. SIN secretos:
                         la identidad la provee la plataforma (Azure).

La interfaz pública es mínima: put_blob / get_blob / list_blobs.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Protocol


# La connection string del emulador Azurite se toma del entorno
# (AZURITE_CONNECTION_STRING). No se hardcodea en el repo: aunque la cuenta/clave
# de desarrollo de Azurite son públicas y bien conocidas, incluirlas dispara los
# secret scanners. Se obtiene de la doc oficial de Microsoft:
# https://learn.microsoft.com/en-us/azure/storage/common/storage-connect-azurite?tabs=blob-storage
# En docker-compose, el BlobEndpoint debe apuntar al host `azurite` de la red
# interna: ...;BlobEndpoint=http://azurite:10000/devstoreaccount1;


class BlobStorage(ABC):
    """Interfaz de storage. Dos implementaciones: Azurite y managed identity."""

    @abstractmethod
    def put_blob(self, name: str, data: bytes, content_type: str) -> str:
        """Escribe un blob y devuelve su nombre/identificador dentro del container."""

    @abstractmethod
    def get_blob(self, name: str) -> bytes:
        """Lee y devuelve el contenido de un blob."""

    @abstractmethod
    def list_blobs(self) -> list[str]:
        """Lista los nombres de los blobs del container."""


class AzureBlobStorage(BlobStorage):
    """
    Implementación única sobre el SDK azure-storage-blob.

    El constructor recibe un BlobServiceClient ya armado (con Azurite o con
    managed identity, según el modo). La lógica de put/get/list es idéntica
    para ambos modos: ése es justamente el punto de usar el SDK real.
    """

    def __init__(self, service_client, container: str) -> None:
        self._service = service_client
        self._container_name = container
        self._ensure_container()

    def _container(self):
        return self._service.get_container_client(self._container_name)

    def _ensure_container(self) -> None:
        try:
            self._container().create_container()
        except Exception:
            # Ya existe (o carrera de creación): idempotente a propósito.
            pass

    def put_blob(self, name: str, data: bytes, content_type: str) -> str:
        from azure.storage.blob import ContentSettings

        blob = self._container().get_blob_client(name)
        blob.upload_blob(
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
        return name

    def get_blob(self, name: str) -> bytes:
        blob = self._container().get_blob_client(name)
        return blob.download_blob().readall()

    def list_blobs(self) -> list[str]:
        return [b.name for b in self._container().list_blobs()]


def build_storage() -> BlobStorage:
    """
    Factory: construye la capa de storage según STORAGE_MODE.

    Es lo único que cambia entre local y Azure. El resto del `processor`
    trabaja siempre contra la interfaz BlobStorage sin saber el modo.
    """
    from azure.storage.blob import BlobServiceClient

    mode = os.getenv("STORAGE_MODE", "azurite").lower()
    container = os.getenv("STORAGE_CONTAINER", "documents")

    if mode == "azurite":
        conn_str = os.getenv("AZURITE_CONNECTION_STRING")
        if not conn_str:
            raise RuntimeError(
                "Falta AZURITE_CONNECTION_STRING. Copiá .env.example a .env y pegá "
                "la connection string de desarrollo de Azurite (ver el link a la "
                "doc de Microsoft en .env.example)."
            )
        service = BlobServiceClient.from_connection_string(conn_str)
        return AzureBlobStorage(service, container)

    if mode == "managed_identity":
        # Producción en Azure: sin connection string, sin secretos.
        # La identidad la resuelve DefaultAzureCredential (managed identity
        # del Container App). Sólo hace falta el endpoint de la cuenta.
        from azure.identity import DefaultAzureCredential

        account_url = os.environ["STORAGE_ACCOUNT_URL"]  # ej: https://<cuenta>.blob.core.windows.net
        credential = DefaultAzureCredential()
        service = BlobServiceClient(account_url=account_url, credential=credential)
        return AzureBlobStorage(service, container)

    raise ValueError(
        f"STORAGE_MODE inválido: {mode!r}. Usá 'azurite' o 'managed_identity'."
    )
