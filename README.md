# DocSafe — App demo de la serie "Arquitectura privada en Azure Container Apps con Terraform"

> [!info] Esta app forma parte de un artículo
> Este repositorio es el código de la app de ejemplo que acompaña al artículo
> **[Arquitectura privada en Azure Container Apps con Terraform](https://kevorojas.com/es/blog/azure-container-apps-arquitectura-privada)**,
> publicado en [kevorojas.com](https://kevorojas.com). Ahí explico, capítulo por
> capítulo, cómo se construye con **Terraform** una arquitectura privada en Azure
> —VNet, sin ingress público, identidades sin secretos, pipelines separados— y
> esta app es la "carga útil" que se hostea dentro de esa infraestructura.
>
> Si llegaste directo al repo, te recomiendo **empezar por el artículo** para
> tener el contexto completo: acá está solo la app; la estrella de la serie es la
> infraestructura que la protege.

Esta es la **app de ejemplo** ("la carga útil") que corre *dentro* de la
arquitectura que construimos en la serie de YouTube de KevoRojas. Es una app
mínima de **procesamiento de documentos**: subís un archivo, se valida, se
guarda en un blob storage y aparece en un catálogo.

La app existe para poner a prueba la infraestructura real (comunicación
service-to-service, storage privado, una única puerta pública). Es simple a
propósito: **la estrella de la serie es la infra, no la app**.

> [!important]
> Este `docker-compose` muestra **SOLO la app funcionando**. **NO** reproduce
> la capa de seguridad de Azure. En local **no hay** VNet, ni managed identity,
> ni private endpoints, ni Application Gateway/WAF. Todo eso se construye en la
> serie con **Terraform, en otro repo**. Acá los servicios simplemente corren
> en la red interna de Docker. El mapeo es **1:1**: cada servicio de este
> compose es el mismo que después vive en un Azure Container App (misma app,
> distinto hosting).

---

## Los 4 servicios

Un solo lenguaje: **Python 3.11 + FastAPI** en los cuatro. Cada uno tiene su
propio Dockerfile.

| Servicio     | Rol                                                                 | ¿Puerto al host? |
|--------------|---------------------------------------------------------------------|------------------|
| **`web`**    | Frontend / BFF. FastAPI + Jinja2 + HTMX (server-rendered, sin SPA). | **Sí** (`8080`)  |
| **`api`**    | Backend interno (JSON). Orquesta processor y catalog.               | No               |
| **`processor`** | Valida el documento (hash, tamaño, tipo) y lo escribe en storage. | No             |
| **`catalog`** | Índice/metadata de los documentos (SQLite en un volumen).          | No               |

Además corre **Azurite**, el emulador oficial de Azure Blob Storage (tampoco
expone puerto al host).

El **`web` es el único servicio con puerto expuesto**. Emula el modelo de
"una única puerta pública": en Azure, sólo el `web` tiene ingress (detrás del
Application Gateway con WAF); todo lo demás es privado (intra-VNet). El `web`
**nunca** toca el storage ni el catálogo directamente: siempre pasa por el `api`.

---

## Flujo

```
                          (única puerta pública)
   ┌─────────┐   HTTP    ┌───────┐   HTTP    ┌───────────┐   SDK    ┌──────────┐
   │ usuario │ ────────► │  web  │ ────────► │    api    │ ───────► │processor │ ──┐
   │ browser │  :8080    │ (BFF) │  interno  │ (interno) │ interno  │(interno) │   │
   └─────────┘           └───────┘           └─────┬─────┘          └────┬─────┘   │
                                                   │                     │         │ put_blob
                                                   │ GET /documents      │ registra│
                                                   ▼                     ▼         ▼
                                             ┌───────────┐         ┌──────────┐  ┌─────────┐
                                             │  catalog  │◄────────│ (avisa)  │  │ Azurite │
                                             │ (interno) │         └──────────┘  │ (blob)  │
                                             └───────────┘                       └─────────┘

  Subir:   usuario → web → api → processor → storage (Azurite)   [+ processor → catalog]
  Listar:  usuario → web → api → catalog
```

- **Subir un documento:** `web → api → processor`. El `processor` valida
  (SHA-256, tamaño máximo, tipos permitidos: PDF/TXT/PNG/JPG), escribe el blob
  en el storage y le avisa al `catalog` para que registre la metadata.
- **Listar el catálogo:** `web → api → catalog`. La tabla se refresca vía HTMX
  sin recargar toda la página.

---

## Cómo levantarla

Requisitos: Docker + Docker Compose.

**1. Configurá el `.env`** (una sola vez). La app usa el emulador Azurite para el
storage. Su connection string de desarrollo no viene en el repo (para no disparar
secret scanners), así que copiá el archivo de ejemplo y pegá la string desde la
[doc oficial de Microsoft](https://learn.microsoft.com/en-us/azure/storage/common/storage-connect-azurite?tabs=blob-storage):

```bash
cp .env.example .env
# editá .env y completá AZURITE_CONNECTION_STRING (ver el link de arriba).
# Recordá: el BlobEndpoint debe apuntar al host `azurite`, no a 127.0.0.1:
#   ...;BlobEndpoint=http://azurite:10000/devstoreaccount1;
```

**2. Levantá todo:**

```bash
docker compose up --build
```

Después abrí el navegador en:

```
http://localhost:8080
```

Subí un archivo (PDF, TXT, PNG o JPG) y miralo aparecer en el catálogo.

Para bajarla:

```bash
docker compose down            # conserva los volúmenes (Azurite + catálogo)
docker compose down -v         # borra también los datos
```

---

## Storage: local vs. Azure (misma capa de código)

El `processor` escribe en el blob storage a través de una capa de abstracción
(`processor/storage.py`) que usa el **SDK real `azure-storage-blob`** en ambos
entornos. Sólo cambia cómo se construye el cliente, según `STORAGE_MODE`:

| `STORAGE_MODE`      | Entorno    | Cómo autentica                                                        |
|---------------------|------------|-----------------------------------------------------------------------|
| `azurite` (default) | Local      | Connection string de desarrollo de Azurite (público, no es secreto).  |
| `managed_identity`  | Azure (prod)| `DefaultAzureCredential` (managed identity). **Sin connection strings.** |

En este repo sólo usamos `azurite`. El modo `managed_identity` ya está escrito
y comentado para cuando la app corra en Azure: no necesita secretos, sólo
`STORAGE_ACCOUNT_URL`. Así el código es idéntico al de producción.

---

## Variables de entorno

Están documentadas en [`.env.example`](./.env.example). Las URLs internas de cada
servicio y el modo de storage ya vienen seteadas en `docker-compose.yml`. La única
que tenés que completar a mano es `AZURITE_CONNECTION_STRING` en tu `.env` (ver el
paso 1 de [Cómo levantarla](#cómo-levantarla)); no se versiona para no incluir la
clave de desarrollo de Azurite en el repo.

---

## Estructura del repo

```
container-apps-demo/
├── web/          FastAPI + Jinja2 + HTMX + static (única puerta pública)
├── api/          FastAPI — backend interno que orquesta
├── processor/    FastAPI — validación + capa de storage (Azurite / managed identity)
├── catalog/      FastAPI — índice/metadata (SQLite en volumen)
├── docker-compose.yml
├── .env.example
└── README.md
```
