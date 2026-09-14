"""
run_model.py - Benchmark unificado para cualquier modelo
========================================================
Reemplaza test_7.py, test_gpt56.py, test_gemini38.py, test_glm53.py

Uso:
    python benchmark/run_model.py deepseek/deepseek-v4-flash-0731
    python benchmark/run_model.py openai/gpt-5.6-luna
    python benchmark/run_model.py google/gemini-3.8-flash
    python benchmark/run_model.py z-ai/glm-5.3-flash
"""

import sys
import os
import asyncio
import json
import time
import logging
from pathlib import Path
from openai import AsyncOpenAI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
from prompt import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from llm import parse_llm_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MAX_CONCURRENT = 10
MAX_RETRIES = 3
BASE_DELAY = 5

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
MD_DIR = PROJECT_ROOT / "temp" / "markdown_cleaned"
OUT_DIR = PROJECT_ROOT / "temp" / "results"


def get_stats():
    return {
        "processed": 0,
        "errors": 0,
        "skipped": 0,
        "total_tokens_in": 0,
        "total_tokens_out": 0,
        "total_records": 0,
        "start_time": None,
    }


def log_progress(stats, total):
    elapsed = time.time() - stats["start_time"]
    done = stats["processed"] + stats["errors"] + stats["skipped"]
    if stats["processed"] > 0:
        avg = elapsed / stats["processed"]
        remaining = (total - done) * avg
        eta = f"{remaining:.0f}s ({remaining/60:.1f}min)"
    else:
        eta = "calculando..."
    logger.info(
        f"[PROGRESS] {done}/{total} | "
        f"OK:{stats['processed']} ERR:{stats['errors']} SKIP:{stats['skipped']} | "
        f"Tokens: {stats['total_tokens_in']} in / {stats['total_tokens_out']} out | "
        f"Registros WVTR: {stats['total_records']} | "
        f"Elapsed: {elapsed:.0f}s | ETA: {eta}"
    )


async def process_file(client, semaphore, md_path, model, stats, total):
    out_path = OUT_DIR / f"{md_path.stem}__{model.split('/')[-1]}.json"
    if out_path.exists():
        logger.info(f"[SKIP] {md_path.name} (ya existe)")
        stats["skipped"] += 1
        log_progress(stats, total)
        return json.loads(out_path.read_text())

    content = md_path.read_text()
    tokens_aprox = len(content) // 4
    logger.info(f"[START] {md_path.name} ({len(content)} chars, ~{tokens_aprox} tokens)")

    user_prompt = USER_PROMPT_TEMPLATE.format(
        doi="N/A", pdf_filename=md_path.name, markdown_content=content
    )

    async with semaphore:
        start = time.time()
        timeout = 120 if tokens_aprox < 5000 else 300 if tokens_aprox < 10000 else 480
        response = None
        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0,
                    ),
                    timeout=timeout,
                )
                break
            except asyncio.TimeoutError:
                last_error = f"timeout ({timeout}s)"
                logger.warning(f"[RETRY {attempt+1}/{MAX_RETRIES}] {md_path.name}: {last_error}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(BASE_DELAY * (attempt + 1))
            except Exception as e:
                last_error = str(e)
                is_rate_limit = "429" in last_error or "rate" in last_error.lower()
                if is_rate_limit:
                    delay = BASE_DELAY * (2 ** attempt)
                    logger.warning(f"[RETRY {attempt+1}/{MAX_RETRIES}] {md_path.name}: rate limit, esperando {delay}s")
                    await asyncio.sleep(delay)
                elif attempt < MAX_RETRIES - 1:
                    logger.warning(f"[RETRY {attempt+1}/{MAX_RETRIES}] {md_path.name}: {last_error}")
                    await asyncio.sleep(BASE_DELAY)
                else:
                    break

        if response is None:
            logger.error(f"[FAIL] {md_path.name}: {last_error}")
            stats["errors"] += 1
            log_progress(stats, total)
            return {"file": md_path.name, "error": last_error}

    latency = time.time() - start
    raw = response.choices[0].message.content
    tokens_in = response.usage.prompt_tokens
    tokens_out = response.usage.completion_tokens
    stats["total_tokens_in"] += tokens_in
    stats["total_tokens_out"] += tokens_out

    logger.info(f"[RESPONSE] {md_path.name}: {tokens_in} in / {tokens_out} out tokens, {latency:.1f}s")

    try:
        result = parse_llm_json(raw)
        n_registros = len(result.get("registros", []))
        stats["total_records"] += n_registros
        logger.info(f"[PARSE OK] {md_path.name}: {n_registros} registros WVTR extraidos")
    except Exception as e:
        logger.warning(f"[PARSE ERROR] {md_path.name}: {e}")
        result = {"file": md_path.name, "parse_error": str(e), "raw": raw}

    result["_meta"] = {
        "latency": round(latency, 2),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "model": model,
    }
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    logger.info(f"[DONE] {md_path.name} en {latency:.1f}s -> {out_path.name}")

    stats["processed"] += 1
    log_progress(stats, total)
    return result


async def main(model):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stats = get_stats()
    stats["start_time"] = time.time()

    md_files = sorted(MD_DIR.glob("*.md"), key=lambda f: f.stat().st_size)
    if not md_files:
        logger.error(f"No se encontraron archivos .md en {MD_DIR}")
        return

    total = len(md_files)
    logger.info(f"{'='*50}")
    logger.info(f"BENCHMARK - {model}")
    logger.info(f"{'='*50}")
    logger.info(f"Archivos: {total} (ordenados por tamaño: pequeño -> grande)")
    logger.info(f"Concurrencia: {MAX_CONCURRENT}")
    logger.info(f"Max reintentos: {MAX_RETRIES}")
    logger.info(f"Resultados en: {OUT_DIR}")
    logger.info(f"{'='*50}")

    for i, f in enumerate(md_files, 1):
        size = f.stat().st_size
        tokens = size // 4
        logger.info(f"  [{i}/{total}] {f.name} ({size:,} chars, ~{tokens:,} tokens)")

    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    tasks = [process_file(client, semaphore, md, model, stats, total) for md in md_files]
    results = await asyncio.gather(*tasks)

    total_latency = time.time() - stats["start_time"]

    logger.info(f"")
    logger.info(f"{'='*50}")
    logger.info(f"RESUMEN FINAL - {model}")
    logger.info(f"{'='*50}")
    logger.info(f"Exitosos:    {stats['processed']}/{total}")
    logger.info(f"Errores:     {stats['errors']}")
    logger.info(f"Saltados:    {stats['skipped']}")
    logger.info(f"Registros:   {stats['total_records']} WVTR")
    logger.info(f"Tokens:      {stats['total_tokens_in']} in / {stats['total_tokens_out']} out")
    logger.info(f"Tiempo:      {total_latency:.1f}s ({total_latency/60:.1f} min)")
    if stats["processed"] > 0:
        logger.info(f"Promedio:    {total_latency/stats['processed']:.1f}s/archivo")
    logger.info(f"{'='*50}")


def main(model=None):
    if model is None:
        if len(sys.argv) < 2:
            print("Uso: benchmark-run <model>")
            print("Ejemplos:")
            print("  benchmark-run deepseek/deepseek-v4-flash-0731")
            print("  benchmark-run openai/gpt-5.6-luna")
            print("  benchmark-run google/gemini-3.8-flash")
            print("  benchmark-run z-ai/glm-5.3-flash")
            sys.exit(1)
        model = sys.argv[1]
    
    try:
        asyncio.run(main(model))
    except RuntimeError:
        import nest_asyncio
        nest_asyncio.apply()
        asyncio.get_event_loop().run_until_complete(main(model))


if __name__ == "__main__":
    main()
