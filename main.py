import sys
import os
import json
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "src")

from cloudfareR2 import get_s3_client, list_pdfs, download_pdf, R2ConnectionError, DownloadError
from pdf_to_markdown import pdf_to_markdown, ConversionError
from clean_markdown import extract_doi, clean_markdown, CleanError
from llm import get_client, extract_wvtr_with_retry, LLMError
from database import get_connection, insert_wvtr, get_stats, DatabaseError

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
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    return {"total_pdfs": 0, "last_batch": 0, "last_index": 0, "completed": [], "failed": []}


def save_progress(progress):
    with file_lock:
        with open(PROGRESS_FILE, "w") as f:
            json.dump(progress, f, indent=2)


def load_errors():
    if os.path.exists(ERRORS_FILE):
        with open(ERRORS_FILE, "r") as f:
            return json.load(f)
    return []


def save_errors(errors):
    with file_lock:
        with open(ERRORS_FILE, "w") as f:
            json.dump(errors, f, indent=2)


def add_error(errors, pdf_key, error_type, error_msg):
    errors.append({
        "pdf_key": pdf_key,
        "error_type": error_type,
        "error_msg": str(error_msg),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    })


def load_dois():
    if os.path.exists(DOIS_FILE):
        with open(DOIS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_dois(dois):
    with file_lock:
        with open(DOIS_FILE, "w") as f:
            json.dump(dois, f, indent=2)


def load_llm_results():
    if os.path.exists(LLM_RESULTS_FILE):
        with open(LLM_RESULTS_FILE, "r") as f:
            return json.load(f)
    return []


def save_llm_results(results):
    with file_lock:
        with open(LLM_RESULTS_FILE, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)


def add_llm_result(results, pdf_key, doi, llm_result):
    results.append({
        "pdf_key": pdf_key,
        "doi": doi,
        "article_title": llm_result.get("article_title"),
        "registros": llm_result.get("registros", []),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    })


def process_single_pdf(s3_client, pdf_key, errors):
    nombre = pdf_key.split("/")[-1]
    local_pdf = f"{TEMP_PDFS}/{nombre}"
    start = time.time()

    for attempt in range(MAX_RETRIES + 1):
        try:
            download_pdf(s3_client, pdf_key, local_pdf)
            md_path = pdf_to_markdown(local_pdf, TEMP_MARKDOWN)

            with open(md_path, "r") as f:
                raw_md = f.read()

            doi = extract_doi(raw_md)
            cleaned_md = clean_markdown(raw_md)

            nombre_md = Path(md_path).stem
            cleaned_path = f"{TEMP_MARKDOWN_CLEANED}/{nombre_md}.md"
            with open(cleaned_path, "w") as f:
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

    total = progress["total_pdfs"]
    completed = len(progress["completed"])
    failed = len(progress["failed"])

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
# MAIN
# ============================================

def main():
    ensure_dirs()
    start_time = time.time()

    s3 = get_s3_client()
    pdfs = list_pdfs(s3)
    total = len(pdfs)
    print(f"PDFs encontrados en R2: {total}")

    progress = load_progress()
    progress["total_pdfs"] = total
    completed_set = set(progress["completed"])

    pending_pdfs = [p for p in pdfs if p not in completed_set]
    print(f"PDFs pendientes: {len(pending_pdfs)}")
    print(f"Workers paralelos: {MAX_WORKERS}")

    errors = load_errors()
    dois = load_dois()

    # FASE 1: Descargar y convertir PDFs a markdown
    print("\n" + "=" * 50)
    print("  FASE 1: Descarga y conversión de PDFs")
    print("=" * 50)

    total_batches = (len(pending_pdfs) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_num in range(total_batches):
        batch_start = batch_num * BATCH_SIZE
        batch_end = batch_start + BATCH_SIZE
        batch = pending_pdfs[batch_start:batch_end]

        print(f"\n--- Batch {batch_num + 1}/{total_batches} ({len(batch)} PDFs) ---")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(process_single_pdf, s3, pdf_key, errors): pdf_key
                for pdf_key in batch
            }

            for future in as_completed(futures):
                success, md_path, elapsed, pdf_key, doi = future.result()
                nombre = pdf_key.split("/")[-1]

                if success:
                    with file_lock:
                        progress["completed"].append(pdf_key)
                        dois[pdf_key] = doi
                    doi_display = doi if doi else "sin DOI"
                    print(f"  OK   {nombre} ({elapsed}s) [{doi_display}]")
                else:
                    with file_lock:
                        progress["failed"].append(pdf_key)
                    print(f"  FAIL {nombre} ({elapsed}s)")

                save_progress(progress)
                save_errors(errors)
                save_dois(dois)

        progress["last_batch"] = batch_num + 1
        progress["last_index"] = batch_start + len(batch)
        save_progress(progress)

        print(f"  Batch {batch_num + 1} completado.")

    # FASE 2: Procesar markdowns con LLM
    print("\n" + "=" * 50)
    print("  FASE 2: Extracción WVTR con LLM")
    print("=" * 50)

    llm_client = get_client()
    llm_results = load_llm_results()
    processed_keys = {r["pdf_key"] for r in llm_results}

    completed_markdowns = []
    for pdf_key in progress["completed"]:
        if pdf_key not in processed_keys:
            nombre = Path(pdf_key).stem
            md_path = f"{TEMP_MARKDOWN_CLEANED}/{nombre}.md"
            if os.path.exists(md_path):
                completed_markdowns.append((pdf_key, md_path, dois.get(pdf_key)))

    print(f"Markdowns pendientes para LLM: {len(completed_markdowns)}")
    print(f"Lote LLM: {LLM_BATCH_SIZE} markdowns")

    llm_batches = [
        completed_markdowns[i:i + LLM_BATCH_SIZE]
        for i in range(0, len(completed_markdowns), LLM_BATCH_SIZE)
    ]

    for llm_batch_num, llm_batch in enumerate(llm_batches):
        print(f"\n--- LLM Batch {llm_batch_num + 1}/{len(llm_batches)} ---")

        for pdf_key, md_path, doi in llm_batch:
            nombre = Path(pdf_key).stem
            print(f"  Procesando: {nombre}...", end=" ")

            try:
                with open(md_path, "r") as f:
                    markdown_text = f.read()

                llm_result = extract_wvtr_with_retry(
                    llm_client, markdown_text, doi, nombre
                )

                with file_lock:
                    add_llm_result(llm_results, pdf_key, doi, llm_result)
                    save_llm_results(llm_results)

                n_registros = len(llm_result.get("registros", []))
                print(f"OK ({n_registros} registros)")

            except LLMError as e:
                print(f"FALLÓ ({e})")
                add_error(errors, pdf_key, "LLMError", e)
                save_errors(errors)

            time.sleep(3)

        print(f"  LLM Batch {llm_batch_num + 1} completado.")

    # FASE 3: Insertar en PostgreSQL
    print("\n" + "=" * 50)
    print("  FASE 3: Insertar en PostgreSQL")
    print("=" * 50)

    try:
        conn = get_connection()
        print("Conectado a PostgreSQL")

        total_inserted = 0
        for result in llm_results:
            try:
                n = insert_wvtr(
                    conn,
                    article_title=result.get("article_title"),
                    doi=result.get("doi"),
                    pdf_url=result.get("pdf_key", ""),
                    llm_result={"registros": result.get("registros", [])},
                    raw_json=result
                )
                total_inserted += n
            except DatabaseError as e:
                print(f"  Error insertando {result.get('pdf_key')}: {e}")
                continue

        stats = get_stats(conn)
        conn.close()

        print(f"Registros insertados: {total_inserted}")
        print(f"Total en BD: {stats['total_registros']}")
        print(f"DOIs únicos: {stats['dois_unicos']}")
        print(f"Polímeros únicos: {stats['polimeros_unicos']}")

    except DatabaseError as e:
        print(f"Error de conexión a BD: {e}")

    # REPORTE FINAL
    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    total_registros = sum(len(r.get("registros", [])) for r in llm_results)

    print("\n" + "=" * 50)
    print("         RESUMEN FINAL")
    print("=" * 50)
    print(f"PDFs procesados:     {len(progress['completed'])}")
    print(f"PDFs fallidos:       {len(progress['failed'])}")
    print(f"DOIs extraídos:      {len([d for d in dois.values() if d])}")
    print(f"Registros WVTR:      {total_registros}")
    print(f"Tiempo total:        {minutes} min {seconds} sec")
    print("=" * 50)


if __name__ == "__main__":
    main()
