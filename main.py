import argparse
import sys
import os
import json
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from cloudfareR2 import get_s3_client, list_pdf_objects, download_pdf, R2ConnectionError, DownloadError
from pdf_to_markdown import pdf_to_markdown, ConversionError
from clean_markdown import extract_doi, clean_markdown, CleanError
from llm import get_client, extract_wvtr_with_retry, LLMError
from database import (
    get_connection, get_stats, release_connection, DatabaseError,
    ensure_schema, replace_document, prune_stale,
)
from sync_state import (
    migrate_legacy, diff, mark_completed, mark_failed, adopt, mark_pruned,
    completed_keys, failed_keys,
)

# ============================================
# CONFIGURACIÓN
# ============================================
BATCH_SIZE = 50
LLM_BATCH_SIZE = 10
MAX_RETRIES = 1
MAX_WORKERS = 5
TEMP_PDFS = "temp/pdfs"
TEMP_MARKDOWN = "temp/markdown"
TEMP_MARKDOWN_CLEANED = "temp/markdown_cleaned"
PROGRESS_FILE = "temp/progress.json"
ERRORS_FILE = "temp/errors.json"
DOIS_FILE = "temp/dois.json"
LLM_RESULTS_FILE = "temp/llm_results.json"

# Lock para manejar acceso concurrente a archivos
file_lock = threading.Lock()


# ============================================
# FUNCIONES AUXILIARES
# ============================================

def ensure_dirs():
    Path(TEMP_PDFS).mkdir(parents=True, exist_ok=True)
    Path(TEMP_MARKDOWN).mkdir(parents=True, exist_ok=True)
    Path(TEMP_MARKDOWN_CLEANED).mkdir(parents=True, exist_ok=True)


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        dois = load_dois()
        return migrate_legacy(raw, dois)
    return {"version": 2, "total_pdfs": 0, "last_batch": 0, "last_index": 0, "files": {}}


def save_progress(progress):
    with file_lock:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(progress, f, indent=2, ensure_ascii=False)


def load_errors():
    if os.path.exists(ERRORS_FILE):
        with open(ERRORS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_errors(errors):
    with file_lock:
        with open(ERRORS_FILE, "w", encoding="utf-8") as f:
            json.dump(errors, f, indent=2, ensure_ascii=False)


errors_lock = threading.Lock()


def add_error(errors, pdf_key, error_type, error_msg):
    entry = {
        "pdf_key": pdf_key,
        "error_type": error_type,
        "error_msg": str(error_msg),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    with errors_lock:
        errors.append(entry)


def load_dois():
    if os.path.exists(DOIS_FILE):
        with open(DOIS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_dois(dois):
    with file_lock:
        with open(DOIS_FILE, "w", encoding="utf-8") as f:
            json.dump(dois, f, indent=2, ensure_ascii=False)


def load_llm_results():
    if os.path.exists(LLM_RESULTS_FILE):
        with open(LLM_RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_llm_results(results):
    with file_lock:
        with open(LLM_RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)


def add_llm_result(results, pdf_key, doi, etag, llm_result):
    # Reemplaza entrada previa del mismo pdf_key (idempotente)
    results[:] = [r for r in results if r.get("pdf_key") != pdf_key]
    results.append({
        "pdf_key": pdf_key,
        "doi": doi,
        "etag": etag,
        "article_title": llm_result.get("article_title"),
        "registros": llm_result.get("registros", []),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    })


def cleaned_md_path(pdf_key):
    return f"{TEMP_MARKDOWN_CLEANED}/{Path(pdf_key).stem}.md"


def invalidate_derived(pdf_key):
    """Borra derivados de un PDF modificado para forzar regeneración."""
    for p in [cleaned_md_path(pdf_key)]:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def backfill_llm_etag(pdf_key, etag):
    """Propaga el ETag adoptado a resultados LLM existentes (evita re-llamadas)."""
    results = load_llm_results()
    changed = False
    for r in results:
        if r.get("pdf_key") == pdf_key and r.get("etag") != etag:
            r["etag"] = etag
            changed = True
    if changed:
        save_llm_results(results)


def adopt_with_artifacts(files, dois, key, etag):
    """Adopta un PDF ya convertido: DOI desde el markdown existente + backfill LLM."""
    doi = dois.get(key)
    try:
        with open(cleaned_md_path(key), "r", encoding="utf-8", errors="replace") as f:
            doi = extract_doi(f.read()) or doi
    except OSError:
        pass
    adopt(files, key, etag, doi)
    if doi:
        dois[key] = doi
    backfill_llm_etag(key, etag)


def process_single_pdf(s3_client, pdf_key, errors):
    nombre = pdf_key.split("/")[-1]
    local_pdf = f"{TEMP_PDFS}/{nombre}"
    start = time.time()

    for attempt in range(MAX_RETRIES + 1):
        try:
            download_pdf(s3_client, pdf_key, local_pdf)
            md_path = pdf_to_markdown(local_pdf, TEMP_MARKDOWN)

            with open(md_path, "r", encoding="utf-8", errors="replace") as f:
                raw_md = f.read()

            doi = extract_doi(raw_md)
            cleaned_md = clean_markdown(raw_md)

            nombre_md = Path(md_path).stem
            cleaned_path = f"{TEMP_MARKDOWN_CLEANED}/{nombre_md}.md"
            with open(cleaned_path, "w", encoding="utf-8") as f:
                f.write(cleaned_md)

            elapsed = round(time.time() - start, 1)
            return True, cleaned_path, elapsed, pdf_key, doi

        except (DownloadError, ConversionError, CleanError) as e:
            if attempt < MAX_RETRIES:
                time.sleep(2)
            else:
                elapsed = round(time.time() - start, 1)
                add_error(errors, pdf_key, type(e).__name__, e)
                return False, None, elapsed, pdf_key, None

        except Exception as e:
            elapsed = round(time.time() - start, 1)
            add_error(errors, pdf_key, type(e).__name__, e)
            return False, None, elapsed, pdf_key, None

        finally:
            if os.path.exists(local_pdf):
                try:
                    os.remove(local_pdf)
                except OSError:
                    pass


def print_report(progress, errors, start_time):
    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    files = progress.get("files", {})
    total = progress.get("total_pdfs", 0)
    completed = len(completed_keys(files))
    failed = len(failed_keys(files))

    error_counts = {}
    for err in errors:
        etype = err.get("error_type", "unknown")
        error_counts[etype] = error_counts.get(etype, 0) + 1

    print("\n" + "=" * 50)
    print("         RESUMEN DE PROCESAMIENTO")
    print("=" * 50)
    print(f"Total PDFs:           {total}")
    print(f"Procesados:           {completed}")
    print(f"Fallidos:             {failed}")

    if error_counts:
        print("  ├─ Errores:")
        for i, (etype, count) in enumerate(error_counts.items()):
            prefix = "  └─" if i == len(error_counts) - 1 else "  ├─"
            print(f"  {prefix} {etype}: {count}")

    print(f"Tiempo total:         {minutes} min {seconds} sec")
    print("=" * 50)


# ============================================
# FASES
# ============================================

def run_phase_1(s3, r2_objects, progress, errors, dois):
    """Descarga/conversión incremental: solo new/retry/changed/unverified."""
    print("\n" + "=" * 50)
    print("  FASE 1: Descarga y conversión de PDFs (incremental)")
    print("=" * 50)

    files = progress.get("files", {})
    plan, deleted = diff(r2_objects, files)
    etag_by_key = {o["key"]: o.get("etag") for o in r2_objects}

    # Adopción sin costo: entradas legacy/retry con artefactos válidos existentes
    to_process = {}
    adopted = 0
    for key, item in plan.items():
        reason = item["reason"]
        if reason in ("unverified", "retry") and os.path.exists(cleaned_md_path(key)):
            adopt_with_artifacts(files, dois, key, etag_by_key.get(key))
            adopted += 1
        elif reason == "changed":
            invalidate_derived(key)
            to_process[key] = item
        else:
            to_process[key] = item

    print(f"PDFs en R2: {len(r2_objects)} | ya al día: {len(r2_objects) - len(plan)} "
          f"| adoptados sin costo: {adopted} | a procesar: {len(to_process)}")
    for reason in ["new", "retry", "changed", "unverified"]:
        n = sum(1 for v in to_process.values() if v["reason"] == reason)
        if n:
            print(f"  - {reason}: {n}")
    if deleted:
        print(f"  - eliminados de R2 (prune en Fase 3): {len(deleted)}")

    pending = list(to_process.keys())
    print(f"Workers paralelos: {MAX_WORKERS}")
    total_batches = (len(pending) + BATCH_SIZE - 1) // BATCH_SIZE if pending else 0

    for batch_num in range(total_batches):
        batch_start = batch_num * BATCH_SIZE
        batch = pending[batch_start:batch_start + BATCH_SIZE]
        print(f"\n--- Batch {batch_num + 1}/{total_batches} ({len(batch)} PDFs) ---", flush=True)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(process_single_pdf, s3, k, errors): k for k in batch}
            for future in as_completed(futures):
                success, md_path, elapsed, pdf_key, doi = future.result()
                nombre = pdf_key.split("/")[-1]
                with file_lock:
                    if success:
                        mark_completed(files, pdf_key, etag_by_key.get(pdf_key), doi)
                        dois[pdf_key] = doi
                    else:
                        mark_failed(files, pdf_key, etag_by_key.get(pdf_key))
                doi_display = doi if doi else "sin DOI"
                status = "OK  " if success else "FAIL"
                print(f"  {status} {nombre} ({elapsed}s) [{doi_display}]", flush=True)
                save_progress(progress)
                save_errors(errors)
                save_dois(dois)

        progress["last_batch"] = batch_num + 1
        progress["last_index"] = batch_start + len(batch)
        save_progress(progress)
        print(f"  Batch {batch_num + 1} completado.", flush=True)

    return deleted


def run_phase_2(progress, errors, dois):
    """Extracción LLM: solo documentos sin resultado válido para su ETag."""
    print("\n" + "=" * 50)
    print("  FASE 2: Extracción WVTR con LLM (incremental)")
    print("=" * 50)

    files = progress.get("files", {})
    llm_client = get_client()
    llm_results = load_llm_results()
    etag_done = {r["pdf_key"]: r.get("etag") for r in llm_results}

    queue = []
    for pdf_key in completed_keys(files):
        md_path = cleaned_md_path(pdf_key)
        if not os.path.exists(md_path):
            continue
        cur_etag = files[pdf_key].get("etag")
        done_etag = etag_done.get(pdf_key)
        # Re-procesar si cambió el ETag o si nunca se procesó con ETag conocido
        if done_etag is not None and cur_etag is not None and done_etag == cur_etag:
            continue
        queue.append((pdf_key, md_path, dois.get(pdf_key), cur_etag))

    print(f"Markdowns pendientes para LLM: {len(queue)} (con resultado vigente: "
          f"{len(etag_done) - len([q for q in queue if q[0] in etag_done])})")
    print(f"Lote LLM: {LLM_BATCH_SIZE} markdowns")

    llm_batches = [queue[i:i + LLM_BATCH_SIZE] for i in range(0, len(queue), LLM_BATCH_SIZE)]
    for n, batch in enumerate(llm_batches):
        print(f"\n--- LLM Batch {n + 1}/{len(llm_batches)} ---", flush=True)
        for pdf_key, md_path, doi, etag in batch:
            nombre = Path(pdf_key).stem
            print(f"  Procesando: {nombre}...", end=" ", flush=True)
            try:
                with open(md_path, "r", encoding="utf-8", errors="replace") as f:
                    markdown_text = f.read()
                llm_result = extract_wvtr_with_retry(llm_client, markdown_text, doi, nombre)
                with file_lock:
                    add_llm_result(llm_results, pdf_key, doi, etag, llm_result)
                    save_llm_results(llm_results)
                print(f"OK ({len(llm_result.get('registros', []))} registros)", flush=True)
            except LLMError as e:
                print(f"FALLÓ ({e})", flush=True)
                add_error(errors, pdf_key, "LLMError", e)
                save_errors(errors)
            time.sleep(3)
        print(f"  LLM Batch {n + 1} completado.", flush=True)
    return llm_results


def run_phase_3(llm_results, r2_keys, dois, do_prune=True):
    """Upsert idempotente + prune. Con progreso por documento (no se cuelga en silencio)."""
    print("\n" + "=" * 50)
    print("  FASE 3: Sincronizar PostgreSQL (upsert + prune)")
    print("=" * 50)

    print("Conectando a PostgreSQL (timeout 10s)...", flush=True)
    conn = get_connection()
    print("Conectado a PostgreSQL", flush=True)
    try:
        ensure_schema(conn)
        total = len(llm_results)
        replaced = errors_n = 0
        for i, result in enumerate(llm_results, 1):
            if "error" in result or "parse_error" in result:
                continue
            label = (result.get("article_title") or result.get("pdf_key", ""))[:45]
            print(f"  [{i}/{total}] {label}...", end=" ", flush=True)
            try:
                n = replace_document(
                    conn,
                    article_title=result.get("article_title"),
                    doi=result.get("doi"),
                    pdf_url=result.get("pdf_key", ""),
                    llm_result={"registros": result.get("registros", [])},
                    raw_json=result,
                )
                replaced += n
                print(f"OK ({n})", flush=True)
            except DatabaseError as e:
                errors_n += 1
                print(f"ERROR ({e})", flush=True)
        pruned = prune_stale(conn, r2_keys, list(dois.values())) if do_prune else 0
        stats = get_stats(conn)
        print(f"Registros (upsert): {replaced} | podados: {pruned} | errores: {errors_n}")
        print(f"Total en BD: {stats['total_registros']}")
        print(f"DOIs únicos: {stats['dois_unicos']}")
        print(f"Polímeros únicos: {stats['polimeros_unicos']}")
        return stats
    finally:
        release_connection(conn)


# ============================================
# MAIN
# ============================================

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Pipeline WVTR: R2 -> Markdown -> LLM -> PostgreSQL")
    p.add_argument("--phase", choices=["1", "2", "3", "all"], default="all",
                   help="Fase a ejecutar (default: all)")
    p.add_argument("--dry-run", action="store_true",
                   help="Fase 1/2: calcula el plan sin descargar ni llamar al LLM")
    p.add_argument("--no-prune", action="store_true",
                   help="Fase 3: no borrar filas de PDFs eliminados de R2")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    ensure_dirs()
    start_time = time.time()

    try:
        s3 = get_s3_client()
        r2_objects = list_pdf_objects(s3)
    except R2ConnectionError as e:
        print(f"Error conectando a R2: {e}")
        return
    print(f"PDFs encontrados en R2: {len(r2_objects)}")

    progress = load_progress()
    progress["total_pdfs"] = len(r2_objects)
    errors = load_errors()
    dois = load_dois()
    save_progress(progress)

    llm_results = load_llm_results()
    deleted = []

    if args.phase in ("1", "all"):
        if args.dry_run:
            plan, deleted = diff(r2_objects, progress.get("files", {}))
            print(f"[dry-run] a procesar: {len(plan)}, eliminados: {len(deleted)}")
            for k, v in plan.items():
                print(f"  {v['reason']:>10}  {k}")
        else:
            deleted = run_phase_1(s3, r2_objects, progress, errors, dois)
            llm_results = load_llm_results()  # recarga por si Fase 1 invalidó derivados

    if args.phase in ("2", "all") and not args.dry_run:
        llm_results = run_phase_2(progress, errors, dois)

    if args.phase in ("3", "all") and not args.dry_run:
        try:
            run_phase_3(llm_results, [o["key"] for o in r2_objects], dois,
                        do_prune=not args.no_prune)
            for key in deleted:
                mark_pruned(progress.get("files", {}), key)
            save_progress(progress)
        except DatabaseError as e:
            print(f"Error de conexión a BD: {e}")

    # REPORTE FINAL
    files = progress.get("files", {})
    elapsed = time.time() - start_time
    total_registros = sum(len(r.get("registros", [])) for r in llm_results if "registros" in r)
    print("\n" + "=" * 50)
    print("         RESUMEN FINAL")
    print("=" * 50)
    print(f"PDFs procesados:     {len(completed_keys(files))}")
    print(f"PDFs fallidos:       {len(failed_keys(files))}")
    print(f"DOIs extraídos:      {len([d for d in dois.values() if d])}")
    print(f"Registros WVTR:      {total_registros}")
    print(f"Tiempo total:        {int(elapsed // 60)} min {int(elapsed % 60)} sec")
    print("=" * 50)


if __name__ == "__main__":
    main()
