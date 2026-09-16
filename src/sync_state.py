"""sync_state.py - Estado incremental del pipeline basado en ETag de R2.

Formato nuevo:
    {"version": 2, "total_pdfs": N, "last_batch": B, "last_index": I,
     "files": {pdf_key: {"etag": str|None, "doi": str|None,
                         "status": "completed"|"failed"|"pruned",
                         "updated_at": str|None}}}

Reglas:
- Clave nueva en R2 -> procesar.
- Clave con ETag distinto -> reprocesar (reemplaza derivados y filas BD).
- Clave con ETag igual -> skip total (sin descarga, sin LLM, sin BD).
- Clave que ya no está en R2 -> candidata a prune en BD.
- Entradas legacy (etag None) -> "unverified": el llamador verifica artefactos
  (markdown limpio + resultado LLM válido) y las adopta sin costo, o las procesa.
"""

import time

STATE_VERSION = 2


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def migrate_legacy(progress: dict, dois: dict = None) -> dict:
    """Convierte {completed:[], failed:[]} al formato con dict por clave."""
    if isinstance(progress.get("files"), dict):
        progress.setdefault("version", STATE_VERSION)
        return progress
    dois = dois or {}
    files = {}
    for key in progress.get("completed", []):
        files[key] = {"etag": None, "doi": dois.get(key),
                      "status": "completed", "updated_at": None}
    for key in progress.get("failed", []):
        if key not in files:
            files[key] = {"etag": None, "doi": dois.get(key),
                          "status": "failed", "updated_at": None}
    return {
        "version": STATE_VERSION,
        "total_pdfs": progress.get("total_pdfs", 0),
        "last_batch": progress.get("last_batch", 0),
        "last_index": progress.get("last_index", 0),
        "files": files,
    }


def diff(r2_objects: list, files: dict):
    """Clasifica cada objeto R2. Retorna (plan, deleted).

    plan: {pdf_key: {"reason": "new"|"retry"|"changed"|"unverified", "obj": {...}}}
    deleted: [pdf_key] en estado completed que ya no existen en R2.
    """
    files = files or {}
    r2_keys = {o["key"] for o in r2_objects}
    plan = {}
    for obj in r2_objects:
        key, etag = obj["key"], obj.get("etag")
        entry = files.get(key)
        if entry is None:
            plan[key] = {"reason": "new", "obj": obj}
        elif entry.get("status") == "failed":
            plan[key] = {"reason": "retry", "obj": obj}
        elif entry.get("etag") is None:
            plan[key] = {"reason": "unverified", "obj": obj}
        elif etag is not None and entry.get("etag") != etag:
            plan[key] = {"reason": "changed", "obj": obj}
        # else: unchanged -> skip silencioso
    deleted = [k for k, e in files.items()
               if e.get("status") == "completed" and k not in r2_keys]
    return plan, deleted


def mark_completed(files: dict, key: str, etag, doi):
    files[key] = {"etag": etag, "doi": doi, "status": "completed",
                  "updated_at": _now()}


def mark_failed(files: dict, key: str, etag):
    prev_doi = (files.get(key) or {}).get("doi")
    files[key] = {"etag": etag, "doi": prev_doi, "status": "failed",
                  "updated_at": _now()}


def adopt(files: dict, key: str, etag, doi):
    """Acepta artefactos existentes como válidos sin reprocesar."""
    files[key] = {"etag": etag, "doi": doi, "status": "completed",
                  "updated_at": _now()}


def mark_pruned(files: dict, key: str):
    entry = files.get(key, {})
    entry.update({"status": "pruned", "updated_at": _now()})
    files[key] = entry


def completed_keys(files: dict):
    return [k for k, e in (files or {}).items() if e.get("status") == "completed"]


def failed_keys(files: dict):
    return [k for k, e in (files or {}).items() if e.get("status") == "failed"]
