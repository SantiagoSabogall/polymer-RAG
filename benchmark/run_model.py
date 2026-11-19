"""Ejecuta un modelo sobre Markdown y guarda resultados evaluables y reanudables."""

import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path

# Soporta también python benchmark/run_model.py desde un checkout.
if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import AsyncOpenAI
from src.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
from src.llm import parse_llm_json
from src.prompt import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)
BENCHMARK_VERSION = 2


def fingerprint(model, content):
    payload = [BENCHMARK_VERSION, model, content, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


async def process_file(client, semaphore, path, model, output_dir, retries=3):
    content = path.read_text(encoding="utf-8")
    digest = fingerprint(model, content)
    cache = output_dir / f"{path.stem}__{digest[:16]}.json"
    if cache.exists():
        try:
            previous = json.loads(cache.read_text(encoding="utf-8"))
            if previous.get("success") and previous.get("fingerprint") == digest:
                logger.info("Reutilizado %s", path.name)
                return previous
        except (ValueError, OSError):
            pass

    entry = {"paper": path.name, "model_id": model, "fingerprint": digest,
             "success": False, "result": None}
    prompt = USER_PROMPT_TEMPLATE.format(doi="N/A", pdf_filename=path.name,
                                        markdown_content=content)
    loop = asyncio.get_running_loop()
    start = loop.time()
    async with semaphore:
        for attempt in range(retries):
            try:
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model,
                        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                                  {"role": "user", "content": prompt}],
                        temperature=0,
                    ), timeout=480)
                raw = response.choices[0].message.content
                entry["raw_response"] = raw
                usage = response.usage
                entry["tokens_input"] = getattr(usage, "prompt_tokens", 0) or 0
                entry["tokens_output"] = getattr(usage, "completion_tokens", 0) or 0
                result = parse_llm_json(raw)
                if not isinstance(result, dict) or not isinstance(result.get("registros"), list):
                    raise ValueError("La respuesta requiere una lista registros")
                entry.update(success=True, result=result)
                entry.pop("error", None)
                break
            except Exception as exc:
                entry["error"] = str(exc)
                if attempt + 1 < retries:
                    await asyncio.sleep(2 ** attempt)
    entry["latency"] = round(loop.time() - start, 2)
    write_json(cache, entry)
    logger.info("%s %s", "OK" if entry["success"] else "ERROR", path.name)
    return entry


async def run_benchmark_async(model, input_dir, output_dir, concurrency=5):
    papers = sorted(input_dir.glob("*.md"))
    if not papers:
        raise ValueError(f"No hay archivos Markdown en {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(concurrency)
    async with AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY,
                           max_retries=0) as client:
        entries = await asyncio.gather(*(
            process_file(client, semaphore, path, model, output_dir) for path in papers))
    # An aggregate per model avoids overwriting simultaneous runs of other models.
    model_key = hashlib.sha256(model.encode()).hexdigest()[:16]
    result_path = output_dir / f"results-{model_key}.json"
    write_json(result_path, {model: entries})
    logger.info("Resultados evaluables: %s", result_path)
    return entries


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", help="Identificador del modelo en OpenRouter")
    parser.add_argument("--input-dir", type=Path, default=Path("benchmark/papers"))
    parser.add_argument("--output-dir", type=Path, default=Path("temp/benchmark"))
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args(argv)
    if not 1 <= args.concurrency <= 20:
        parser.error("--concurrency debe estar entre 1 y 20")
    if not args.input_dir.is_dir() or not any(args.input_dir.glob("*.md")):
        parser.error(f"No hay archivos Markdown en {args.input_dir}")
    logging.basicConfig(level=logging.INFO)
    entries = asyncio.run(run_benchmark_async(
        args.model, args.input_dir, args.output_dir, args.concurrency))
    return 0 if all(entry["success"] for entry in entries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
