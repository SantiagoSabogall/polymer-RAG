"""
main_test.py - Pipeline Completo de Extracción WVTR
====================================================
Modelo: GPT 5.6 Luna (via OpenRouter)

Pipeline:
  FASE 1: Cloudflare R2 → Download PDFs → Parse Markdown → Clean Regex
  FASE 2: Markdown → LLM (GPT 5.6 Luna) → JSON con registros WVTR
  FASE 3: JSON → PostgreSQL (wvtr_data)

Autor: Sebastian Sabogal
"""

import sys
import os
import asyncio
import json
import time
import logging
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import AsyncOpenAI

# ============================================
# CONFIGURACIÓN DE PATHS
# ============================================
# Agrega src/ al path para importar módulos del proyecto
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
from prompt import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from llm import parse_llm_json
from cloudfareR2 import get_s3_client, list_pdfs, download_pdf, R2ConnectionError, DownloadError
from pdf_to_markdown import pdf_to_markdown, ConversionError
from clean_markdown import extract_doi, clean_markdown, CleanError
from database import get_connection, insert_wvtr, get_stats, DatabaseError, is_already_processed, release_connection
from monitoring import StructuredLogger, CostTracker, PerformanceMetrics

# ============================================
# LOGGING
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("temp/pipeline.log", mode="w", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)

# ============================================
# CONFIGURACIÓN DEL PIPELINE
# ============================================
MODEL = "openai/gpt-5.6-luna"
MAX_CONCURRENT_LLM = 10       # Archivos simultáneos al LLM
MAX_WORKERS_DOWNLOAD = 5      # Workers paralelos para descarga
MAX_RETRIES_LLM = 3           # Reintentos LLM por archivo
BASE_DELAY = 5                # Delay base para retry
BATCH_SIZE = 50               # Lotes de descarga desde R2

# Directorios temporales
TEMP_DIR = Path(__file__).parent / "temp"
TEMP_PDFS = TEMP_DIR / "pdfs"
TEMP_MARKDOWN = TEMP_DIR / "markdown"
TEMP_MARKDOWN_CLEANED = TEMP_DIR / "markdown_cleaned"
TEMP_RESULTS = TEMP_DIR / "results"
PROGRESS_FILE = TEMP_DIR / "progress_test.json"
ERRORS_FILE = TEMP_DIR / "errors_test.json"
DOIS_FILE = TEMP_DIR / "dois_test.json"

# Lock para acceso concurrente a archivos
file_lock = threading.Lock()

# Métricas globales del LLM
llm_stats = {
    "processed": 0,
    "errors": 0,
    "skipped": 0,
    "total_tokens_in": 0,
    "total_tokens_out": 0,
    "total_records": 0,
    "start_time": None,
}


# ============================================
# FUNCIONES AUXILIARES - ARCHIVOS
# ============================================

def ensure_dirs():
    """Crea todas las carpetas temporales necesarias."""
    for d in [TEMP_PDFS, TEMP_MARKDOWN, TEMP_MARKDOWN_CLEANED, TEMP_RESULTS]:
        d.mkdir(parents=True, exist_ok=True)


def load_json(filepath: Path, default=None):
    """Carga un archivo JSON. Si no existe, retorna default."""
    if filepath.exists():
        with open(filepath, "r") as f:
            return json.load(f)
    return default if default is not None else {}


def save_json(filepath: Path, data):
    """Guarda datos en un archivo JSON de forma thread-safe."""
    with file_lock:
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def add_error(errors: list, pdf_key: str, error_type: str, error_msg):
    """Agrega un error a la lista de errores con timestamp."""
    errors.append({
        "pdf_key": pdf_key,
        "error_type": error_type,
        "error_msg": str(error_msg),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    })


# ============================================
# PROGRESS TRACKING Y ERROR RECOVERY
# ============================================

def save_pipeline_state(state):
    """Guarda el estado actual del pipeline."""
    state_file = TEMP_DIR / "pipeline_state.json"
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def load_pipeline_state():
    """Carga el estado del pipeline (si existe)."""
    state_file = TEMP_DIR / "pipeline_state.json"
    if state_file.exists():
        with open(state_file) as f:
            return json.load(f)
    return {"completed": [], "failed": [], "last_run": None}


def should_skip(pdf_key, state, dois):
    """Determina si un PDF debe ser saltado."""
    # Ya completado en ejecución anterior
    if pdf_key in state.get("completed", []):
        return True
    # DOI ya en BD
    doi = dois.get(pdf_key)
    if is_already_processed(doi):
        return True
    return False


# ============================================
# FASE 1: DESCARGA Y CONVERSIÓN
# ============================================

def process_single_pdf(s3_client, pdf_key: str, errors: list) -> tuple:
    """
    Procesa un solo PDF desde R2:
    1. Descarga el PDF desde Cloudflare R2
    2. Convierte PDF a Markdown (opendataloader_pdf)
    3. Extrae DOI del markdown crudo
    4. Limpia el markdown (remueve refs, headers, footers, noise)
    5. Guarda markdown limpio en temp/markdown_cleaned/

    Returns:
        (success: bool, cleaned_path: str, elapsed: float, pdf_key: str, doi: str)
    """
    nombre = pdf_key.split("/")[-1]
    local_pdf = str(TEMP_PDFS / nombre)
    start = time.time()

    try:
        # Paso 1: Descargar PDF desde R2
        download_pdf(s3_client, pdf_key, local_pdf)

        # Paso 2: Convertir PDF a Markdown
        md_path = pdf_to_markdown(local_pdf, str(TEMP_MARKDOWN))

        # Paso 3: Leer markdown crudo
        with open(md_path, "r") as f:
            raw_md = f.read()

        # Paso 4: Extraer DOI
        doi = extract_doi(raw_md)

        # Paso 5: Limpiar markdown
        cleaned_md = clean_markdown(raw_md)

        # Paso 6: Guardar markdown limpio
        nombre_md = Path(md_path).stem
        cleaned_path = TEMP_MARKDOWN_CLEANED / f"{nombre_md}.md"
        with open(cleaned_path, "w") as f:
            f.write(cleaned_md)

        elapsed = round(time.time() - start, 1)
        return True, str(cleaned_path), elapsed, pdf_key, doi

    except (DownloadError, ConversionError, CleanError) as e:
        elapsed = round(time.time() - start, 1)
        add_error(errors, pdf_key, type(e).__name__, e)
        return False, None, elapsed, pdf_key, None

    except Exception as e:
        elapsed = round(time.time() - start, 1)
        add_error(errors, pdf_key, type(e).__name__, e)
        return False, None, elapsed, pdf_key, None

    finally:
        # Limpiar PDF descargado (no necesitamos guardarlo)
        if os.path.exists(local_pdf):
            try:
                os.remove(local_pdf)
            except OSError:
                pass


def run_phase_1(pdfs: list, progress: dict, errors: list, dois: dict) -> int:
    """
    FASE 1: Descarga masiva de PDFs desde R2.

    Usa ThreadPoolExecutor para descargar en paralelo.
    Procesa en lotes de BATCH_SIZE para no sobrecargar R2.

    Returns:
        count de PDFs procesados exitosamente
    """
    logger.info("=" * 50)
    logger.info("  FASE 1: Descarga y Conversión de PDFs")
    logger.info("=" * 50)

    completed_set = set(progress.get("completed", []))
    pending_pdfs = [p for p in pdfs if p not in completed_set]

    logger.info(f"PDFs totales en R2: {len(pdfs)}")
    logger.info(f"PDFs ya procesados: {len(completed_set)}")
    logger.info(f"PDFs pendientes: {len(pending_pdfs)}")
    logger.info(f"Workers paralelos: {MAX_WORKERS_DOWNLOAD}")

    if not pending_pdfs:
        logger.info("No hay PDFs pendientes para descargar.")
        return 0

    s3 = get_s3_client()
    total_batches = (len(pending_pdfs) + BATCH_SIZE - 1) // BATCH_SIZE
    processed_count = 0

    for batch_num in range(total_batches):
        batch_start = batch_num * BATCH_SIZE
        batch_end = batch_start + BATCH_SIZE
        batch = pending_pdfs[batch_start:batch_end]

        logger.info(f"\n--- Batch {batch_num + 1}/{total_batches} ({len(batch)} PDFs) ---")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS_DOWNLOAD) as executor:
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
                    processed_count += 1
                    doi_display = doi if doi else "sin DOI"
                    logger.info(f"  OK   {nombre} ({elapsed}s) [{doi_display}]")
                else:
                    with file_lock:
                        progress["failed"].append(pdf_key)
                    logger.error(f"  FAIL {nombre} ({elapsed}s)")

                # Guardar progreso después de cada archivo
                save_json(PROGRESS_FILE, progress)
                save_json(ERRORS_FILE, errors)
                save_json(DOIS_FILE, dois)

        logger.info(f"  Batch {batch_num + 1} completado.")

    return processed_count


# ============================================
# FASE 2: EXTRACCIÓN LLM (ASYNC)
# ============================================

def log_llm_progress(total: int):
    """Log de progreso en tiempo real para la fase LLM."""
    elapsed = time.time() - llm_stats["start_time"]
    done = llm_stats["processed"] + llm_stats["errors"] + llm_stats["skipped"]
    if llm_stats["processed"] > 0:
        avg = elapsed / llm_stats["processed"]
        remaining = (total - done) * avg
        eta = f"{remaining:.0f}s ({remaining/60:.1f}min)"
    else:
        eta = "calculando..."
    logger.info(
        f"[PROGRESS] {done}/{total} | "
        f"OK:{llm_stats['processed']} ERR:{llm_stats['errors']} SKIP:{llm_stats['skipped']} | "
        f"Tokens: {llm_stats['total_tokens_in']} in / {llm_stats['total_tokens_out']} out | "
        f"Registros WVTR: {llm_stats['total_records']} | "
        f"Elapsed: {elapsed:.0f}s | ETA: {eta}"
    )


async def process_file(client: AsyncOpenAI, semaphore: asyncio.Semaphore,
                       md_path: Path, doi: str, total: int) -> dict:
    """
    Procesa un solo markdown con el LLM:
    1. Lee el contenido del markdown
    2. Construye el prompt (system + user)
    3. Envía a GPT 5.6 Luna via OpenRouter
    4. Parsea la respuesta JSON
    5. Guarda el resultado en temp/results/

    Returns:
        dict con el resultado del LLM o error
    """
    out_path = TEMP_RESULTS / f"{md_path.stem}__gpt-5.6-luna.json"

    # Si ya existe el resultado, actualizar DOI si falta y saltarlo
    if out_path.exists():
        existing_data = json.loads(out_path.read_text())
        # Actualizar DOI en metadata si no existe
        if existing_data.get("_meta", {}).get("doi") is None and doi:
            existing_data["_meta"]["doi"] = doi
            out_path.write_text(json.dumps(existing_data, indent=2, ensure_ascii=False))
        logger.info(f"[SKIP] {md_path.name} (ya existe)")
        llm_stats["skipped"] += 1
        log_llm_progress(total)
        return existing_data

    # Leer contenido del markdown
    content = md_path.read_text()
    tokens_aprox = len(content) // 4
    logger.info(f"[START] {md_path.name} ({len(content)} chars, ~{tokens_aprox} tokens)")

    # Construir prompt
    user_prompt = USER_PROMPT_TEMPLATE.format(
        doi=doi or "N/A",
        pdf_filename=md_path.name,
        markdown_content=content
    )

    async with semaphore:
        start = time.time()
        # Timeout dinámico según tamaño del archivo
        timeout = 120 if tokens_aprox < 5000 else 300 if tokens_aprox < 10000 else 480
        response = None
        last_error = None

        # Retry loop con exponential backoff
        for attempt in range(MAX_RETRIES_LLM):
            try:
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=MODEL,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0,
                    ),
                    timeout=timeout,
                )
                break  # Éxito, salir del loop

            except asyncio.TimeoutError:
                last_error = f"timeout ({timeout}s)"
                logger.warning(f"[RETRY {attempt+1}/{MAX_RETRIES_LLM}] {md_path.name}: {last_error}")
                if attempt < MAX_RETRIES_LLM - 1:
                    await asyncio.sleep(BASE_DELAY * (attempt + 1))

            except Exception as e:
                last_error = str(e)
                is_rate_limit = "429" in last_error or "rate" in last_error.lower()
                if is_rate_limit:
                    delay = BASE_DELAY * (2 ** attempt)
                    logger.warning(f"[RETRY {attempt+1}/{MAX_RETRIES_LLM}] {md_path.name}: rate limit, esperando {delay}s")
                    await asyncio.sleep(delay)
                elif attempt < MAX_RETRIES_LLM - 1:
                    logger.warning(f"[RETRY {attempt+1}/{MAX_RETRIES_LLM}] {md_path.name}: {last_error}")
                    await asyncio.sleep(BASE_DELAY)
                else:
                    break

        # Si después de todos los reintentos no hay respuesta
        if response is None:
            logger.error(f"[FAIL] {md_path.name}: {last_error}")
            llm_stats["errors"] += 1
            log_llm_progress(total)
            return {"file": md_path.name, "error": last_error}

    # Procesar respuesta exitosa
    latency = time.time() - start
    raw = response.choices[0].message.content
    tokens_in = response.usage.prompt_tokens
    tokens_out = response.usage.completion_tokens
    llm_stats["total_tokens_in"] += tokens_in
    llm_stats["total_tokens_out"] += tokens_out

    logger.info(f"[RESPONSE] {md_path.name}: {tokens_in} in / {tokens_out} out tokens, {latency:.1f}s")

    # Parsear JSON del LLM
    try:
        result = parse_llm_json(raw)
        n_registros = len(result.get("registros", []))
        llm_stats["total_records"] += n_registros
        logger.info(f"[PARSE OK] {md_path.name}: {n_registros} registros WVTR extraidos")
    except Exception as e:
        logger.warning(f"[PARSE ERROR] {md_path.name}: {e}")
        result = {"file": md_path.name, "parse_error": str(e), "raw": raw}

    # Agregar metadata al resultado
    result["_meta"] = {
        "latency": round(latency, 2),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "model": MODEL,
        "doi": doi,
    }

    # Guardar JSON
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    logger.info(f"[DONE] {md_path.name} en {latency:.1f}s -> {out_path.name}")

    llm_stats["processed"] += 1
    log_llm_progress(total)
    return result


async def run_phase_2(dois: dict) -> list:
    """
    FASE 2: Extracción WVTR con LLM async.

    Procesa todos los markdowns limpios en paralelo (hasta MAX_CONCURRENT_LLM).
    Guarda cada resultado como JSON individual.

    Returns:
        lista de resultados del LLM
    """
    logger.info("=" * 50)
    logger.info("  FASE 2: Extracción WVTR con LLM")
    logger.info("=" * 50)

    llm_stats["start_time"] = time.time()

    # Encontrar markdowns limpios pendientes
    md_files = sorted(TEMP_MARKDOWN_CLEANED.glob("*.md"), key=lambda f: f.stat().st_size)

    if not md_files:
        logger.error(f"No se encontraron markdowns en {TEMP_MARKDOWN_CLEANED}")
        return []

    total = len(md_files)
    logger.info(f"Modelo: {MODEL}")
    logger.info(f"Archivos: {total} (ordenados por tamaño)")
    logger.info(f"Concurrencia: {MAX_CONCURRENT_LLM}")
    logger.info(f"Max reintentos: {MAX_RETRIES_LLM}")

    for i, f in enumerate(md_files, 1):
        size = f.stat().st_size
        tokens = size // 4
        logger.info(f"  [{i}/{total}] {f.name} ({size:,} chars, ~{tokens:,} tokens)")

    # Crear cliente async
    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM)

    # Mapear DOI por nombre de archivo
    doi_map = {}
    for pdf_key, doi in dois.items():
        nombre = Path(pdf_key).stem
        doi_map[nombre] = doi

    # Procesar todos los archivos en paralelo
    tasks = [
        process_file(client, semaphore, md, doi_map.get(md.stem), total)
        for md in md_files
    ]
    results = await asyncio.gather(*tasks)

    total_latency = time.time() - llm_stats["start_time"]

    logger.info("")
    logger.info(f"{'='*50}")
    logger.info(f"RESUMEN FASE 2 - {MODEL}")
    logger.info(f"{'='*50}")
    logger.info(f"Exitosos:    {llm_stats['processed']}/{total}")
    logger.info(f"Errores:     {llm_stats['errors']}")
    logger.info(f"Saltados:    {llm_stats['skipped']}")
    logger.info(f"Registros:   {llm_stats['total_records']} WVTR")
    logger.info(f"Tokens:      {llm_stats['total_tokens_in']} in / {llm_stats['total_tokens_out']} out")
    logger.info(f"Tiempo:      {total_latency:.1f}s ({total_latency/60:.1f} min)")
    if llm_stats["processed"] > 0:
        logger.info(f"Promedio:    {total_latency/llm_stats['processed']:.1f}s/archivo")
    logger.info(f"{'='*50}")

    return results


# ============================================
# FASE 3: BASE DE DATOS
# ============================================

def run_phase_3(llm_results: list, dois: dict) -> dict:
    """
    FASE 3: Insertar resultados en PostgreSQL.

    1. Conecta a PostgreSQL
    2. Para cada resultado del LLM, inserta los registros WVTR
    3. Muestra estadísticas de la BD

    Returns:
        dict con estadísticas de la inserción
    """
    logger.info("=" * 50)
    logger.info("  FASE 3: Insertar en PostgreSQL")
    logger.info("=" * 50)

    try:
        conn = get_connection()
        logger.info("Conectado a PostgreSQL")

        total_inserted = 0
        errors_count = 0

        for result in llm_results:
            # Saltar resultados con error
            if "error" in result or "parse_error" in result:
                continue

            pdf_key = result.get("_meta", {}).get("doi", "") or result.get("file", "")
            doi = result.get("_meta", {}).get("doi")

            try:
                n = insert_wvtr(
                    conn,
                    article_title=result.get("article_title"),
                    doi=doi,
                    pdf_url=pdf_key,
                    llm_result={"registros": result.get("registros", [])},
                    raw_json=result
                )
                total_inserted += n
                if n > 0:
                    logger.info(f"  INSERT: {result.get('article_title', 'Unknown')[:50]}... ({n} registros)")
            except DatabaseError as e:
                errors_count += 1
                logger.error(f"  Error insertando: {e}")
                continue

        # Obtener estadísticas de la BD
        stats = get_stats(conn)
        conn.close()

        logger.info(f"")
        logger.info(f"{'='*50}")
        logger.info(f"RESUMEN FASE 3 - PostgreSQL")
        logger.info(f"{'='*50}")
        logger.info(f"Registros insertados: {total_inserted}")
        logger.info(f"Errores de inserción: {errors_count}")
        logger.info(f"Total en BD:          {stats['total_registros']}")
        logger.info(f"DOIs únicos:          {stats['dois_unicos']}")
        logger.info(f"Polímeros únicos:     {stats['polimeros_unicos']}")
        logger.info(f"{'='*50}")

        return stats

    except DatabaseError as e:
        logger.error(f"Error de conexión a BD: {e}")
        return {"error": str(e)}


# ============================================
# MAIN - ORQUESTADOR DEL PIPELINE
# ============================================

def main():
    """
    Función principal que orquesta las 3 fases del pipeline:
    1. Descarga y conversión de PDFs
    2. Extracción WVTR con LLM
    3. Inserción en PostgreSQL
    """
    ensure_dirs()
    start_time = time.time()

    # Inicializar monitoreo
    structured_logger = StructuredLogger("pipeline")
    cost_tracker = CostTracker()
    performance_metrics = PerformanceMetrics()

    logger.info("=" * 50)
    logger.info("PIPELINE TEST - GPT 5.6 Luna")
    logger.info("=" * 50)

    # ============================================
    # FASE 1: Descarga y Conversión
    # ============================================
    try:
        s3 = get_s3_client()
        pdfs = list_pdfs(s3)
        logger.info(f"PDFs encontrados en R2: {len(pdfs)}")
        structured_logger.log_pipeline_start(len(pdfs))
    except R2ConnectionError as e:
        logger.error(f"Error conectando a R2: {e}")
        return

    # Cargar estado previo
    progress = load_json(PROGRESS_FILE, {
        "total_pdfs": 0, "completed": [], "failed": []
    })
    progress["total_pdfs"] = len(pdfs)
    errors = load_json(ERRORS_FILE, [])
    dois = load_json(DOIS_FILE, {})
    pipeline_state = load_pipeline_state()

    # Ejecutar FASE 1
    performance_metrics.start_phase("phase1")
    phase1_count = run_phase_1(pdfs, progress, errors, dois)
    performance_metrics.end_phase("phase1")
    performance_metrics.metrics["phase1"]["pdfs_processed"] = phase1_count

    # ============================================
    # FASE 2: Extracción LLM
    # ============================================
    # Ejecutar FASE 2 (async)
    performance_metrics.start_phase("phase2")
    llm_results = asyncio.run(run_phase_2(dois))
    performance_metrics.end_phase("phase2")
    performance_metrics.metrics["phase2"]["pdfs_processed"] = llm_stats["processed"]
    performance_metrics.metrics["phase2"]["errors"] = llm_stats["errors"]
    performance_metrics.metrics["phase2"]["total_tokens"] = llm_stats["total_tokens_in"] + llm_stats["total_tokens_out"]

    # ============================================
    # FASE 3: PostgreSQL
    # ============================================
    # Ejecutar FASE 3
    performance_metrics.start_phase("phase3")
    db_stats = run_phase_3(llm_results, dois)
    performance_metrics.end_phase("phase3")
    if "error" not in db_stats:
        performance_metrics.metrics["phase3"]["records_inserted"] = db_stats.get("total_registros", 0)

    # ============================================
    # RESUMEN FINAL
    # ============================================
    total_latency = time.time() - start_time
    minutes = int(total_latency // 60)
    seconds = int(total_latency % 60)

    total_registros = sum(len(r.get("registros", [])) for r in llm_results if "registros" in r)

    # Guardar estado del pipeline
    pipeline_state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_pipeline_state(pipeline_state)

    # Log de métricas
    metrics_summary = performance_metrics.get_summary()
    structured_logger.log_pipeline_end({
        "total_pdfs": len(pdfs),
        "processed": phase1_count,
        "llm_records": total_registros,
        "duration_seconds": total_latency,
        "metrics": metrics_summary
    })

    logger.info("")
    logger.info("=" * 50)
    logger.info("         RESUMEN FINAL DEL PIPELINE")
    logger.info("=" * 50)
    logger.info(f"Modelo:               {MODEL}")
    logger.info(f"PDFs descargados:     {phase1_count}")
    logger.info(f"Markdowns generados:  {len(list(TEMP_MARKDOWN_CLEANED.glob('*.md')))}")
    logger.info(f"Resultados LLM:       {len([r for r in llm_results if 'registros' in r])}")
    logger.info(f"Registros WVTR:       {total_registros}")
    if "error" not in db_stats:
        logger.info(f"Total en BD:          {db_stats.get('total_registros', 0)}")
    logger.info(f"Tiempo total:         {minutes} min {seconds} sec")
    logger.info("")
    logger.info("MÉTRICAS DE RENDIMIENTO:")
    logger.info(f"  FASE 1 (Descarga):  {metrics_summary['phase1_duration']:.1f}s")
    logger.info(f"  FASE 2 (LLM):       {metrics_summary['phase2_duration']:.1f}s")
    logger.info(f"  FASE 3 (BD):        {metrics_summary['phase3_duration']:.1f}s")
    logger.info(f"  Total tokens:       {metrics_summary['phase2_tokens']:,}")
    logger.info("=" * 50)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        if "cannot be called when another loop is running" in str(e):
            import nest_asyncio
            nest_asyncio.apply()
            main()
        else:
            raise
