"""llm_vision.py — Extracción de WVTR desde imágenes con GPT-5.6 Luna.

Se sigue usando el mismo modelo que la ruta de texto: ``openai/gpt-5.6-luna`` ya
declara ``input_modalities: [file, image, text]`` y ``structured_outputs`` en
el catálogo de OpenRouter, así que no hay motivo para introducir un segundo
modelo de extracción. Lo único que cambia es el mensaje: un array de contenido con
``image_url`` en vez de texto plano.

Dos detalles que costaron una llamada de diagnóstico cada uno:

- ``temperature=0`` **no** está en los ``supported_parameters`` de gpt-5.6-luna
  (sí ``seed``). Se usa ``seed`` para reproducibilidad.
- aquí sí se necesita ``detail: "high"``: el extractor tiene que leer dígitos de
  una tabla rasterizada, a diferencia del gate, que solo distingue tabla de
  gráfica.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from openai import AsyncOpenAI

try:
    from .image_manifest import ImageCandidate
    from .llm import parse_llm_json
    from .prompt_vision import (
        VISION_SCHEMA,
        VISION_SYSTEM_PROMPT,
        VISION_USER_PROMPT,
    )
except ImportError:
    from image_manifest import ImageCandidate
    from llm import parse_llm_json
    from prompt_vision import VISION_SCHEMA, VISION_SYSTEM_PROMPT, VISION_USER_PROMPT

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

VISION_MODEL = "openai/gpt-5.6-luna"
VISION_MAX_RETRIES = 3
VISION_BASE_DELAY = 5
#: Tope de imágenes por documento enviadas al extractor. Si el gate deja pasar más
#: (paper con muchas tablas WVTR), se priorizan las de mayor área relativa a la página:
#: una tabla WVTR grande suele ser la principal fuente de datos del artículo.
VISION_MAX_IMAGES_PER_DOC = 6


class VisionError(Exception):
    pass


@dataclass
class ImageExtraction:
    candidate: ImageCandidate
    records: list[dict] = field(default_factory=list)
    article_title: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.candidate.name,
            "page": self.candidate.page,
            "records": len(self.records),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "latency": round(self.latency, 2),
            "error": self.error,
        }


def pick_images(candidates: list[ImageCandidate], limit: int = VISION_MAX_IMAGES_PER_DOC) -> list[ImageCandidate]:
    """Selecciona hasta ``limit`` imágenes, las mayores primero."""
    return sorted(candidates, key=lambda c: -c.area_ratio)[:limit]


async def extract_from_image(
    client: AsyncOpenAI,
    cand: ImageCandidate,
    doi: str = "",
    model: str = VISION_MODEL,
) -> ImageExtraction:
    """Extrae registros WVTR de UNA imagen. No lanza: los errores van en el resultado."""
    out = ImageExtraction(candidate=cand)
    data = Path(cand.path).read_bytes()
    data_uri = "data:image/png;base64," + base64.b64encode(data).decode()

    payload = [
        {"type": "text", "text": VISION_USER_PROMPT.format(
            doi=doi or "N/A",
            image_name=cand.name,
            page=cand.page,
            context=(cand.context or "(no surrounding text)")[:1200],
        )},
        {"type": "image_url", "image_url": {"url": data_uri, "detail": "high"}},
    ]

    start = time.time()
    last_error = None
    for attempt in range(VISION_MAX_RETRIES):
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": VISION_SYSTEM_PROMPT},
                        {"role": "user", "content": payload},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": VISION_SCHEMA,
                    },
                    # gpt-5.6-luna no declara "temperature"; "seed" sí, y da
                    # la misma reproducibilidad que temperature=0.
                    seed=0,
                    extra_body={"provider": {"require_parameters": True}},
                ),
                timeout=300,
            )
            break
        except TypeError as e:
            out.error = f"Llamada invalida: {e}"
            out.latency = time.time() - start
            return out
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
            if attempt < VISION_MAX_RETRIES - 1:
                delay = VISION_BASE_DELAY * (2 ** attempt)
                logger.warning("[VISION RETRY %d/%d] %s: %s", attempt + 1,
                               VISION_MAX_RETRIES, cand.name, last_error)
                await asyncio.sleep(delay)
    else:
        out.error = str(last_error)
        out.latency = time.time() - start
        return out

    raw = response.choices[0].message.content
    usage = getattr(response, "usage", None)
    out.tokens_in = getattr(usage, "prompt_tokens", 0) or 0
    out.tokens_out = getattr(usage, "completion_tokens", 0) or 0
    out.latency = time.time() - start

    try:
        parsed = parse_llm_json(raw)
    except json.JSONDecodeError as e:
        out.error = f"JSON invalido: {e}"
        return out

    out.article_title = (parsed.get("article_title") or "").strip()
    for reg in parsed.get("registros") or []:
        reg.setdefault("source_ref", cand.name)
        reg.setdefault("source_page", cand.page)
        out.records.append(reg)

    logger.info("[VISION] %s (p.%s): %d registros, %d/%d tokens, %.1fs",
                cand.name, cand.page, len(out.records), out.tokens_in,
                out.tokens_out, out.latency)
    return out


async def extract_all(
    client: AsyncOpenAI,
    candidates: list[ImageCandidate],
    doi: str = "",
    model: str = VISION_MODEL,
    concurrency: int = 4,
    limit: int = VISION_MAX_IMAGES_PER_DOC,
) -> tuple[list[ImageExtraction], dict]:
    """Extrae de varias imágenes en paralelo. Devuelve (extracciones, stats)."""
    selected = pick_images(candidates, limit)
    dropped = len(candidates) - len(selected)
    if dropped:
        logger.warning("[VISION] %d imagenes pasan el gate pero se descartan por "
                       "el tope de %d por documento", dropped, limit)

    semaphore = asyncio.Semaphore(concurrency)

    async def one(cand: ImageCandidate) -> ImageExtraction:
        async with semaphore:
            return await extract_from_image(client, cand, doi, model)

    settled = await asyncio.gather(*(one(c) for c in selected), return_exceptions=True)

    extractions: list[ImageExtraction] = []
    for cand, item in zip(selected, settled):
        if isinstance(item, ImageExtraction):
            extractions.append(item)
        else:
            ext = ImageExtraction(candidate=cand, error=str(item))
            extractions.append(ext)
            logger.error("[VISION FAIL] %s: %s", cand.name, item)

    stats = {
        "vision_images": len(selected),
        "vision_images_dropped_cap": dropped,
        "vision_records": sum(len(e.records) for e in extractions),
        "vision_tokens_in": sum(e.tokens_in for e in extractions),
        "vision_tokens_out": sum(e.tokens_out for e in extractions),
        "vision_errors": sum(1 for e in extractions if e.error),
    }
    return extractions, stats
