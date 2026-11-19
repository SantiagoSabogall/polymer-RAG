"""vision_gate.py — Clasificador typesafe "¿esta imagen contieneWVTR?".

Por qué un gate: ``opendataloader`` extrae todas las imágenes del PDF. En un
paper real, 38 imágenes → 14 candidatas reales → y de esas, la mayoría son
gráficas de rheología o micrografías SEM. Mandar las 38 a un extractor cuesta
tokens y además contamina el resultado con ruido. Un clasificador barato que
solo responde sí/no los detiene.

Por qué ``json_schema`` estricto en vez de pedir "sí o no": el prompt dice
"Answer ONLY: yes or no" y los modelos devuelven "Yes.", "yes\n", "Sí.", o un
párrafo justificando. Eso obliga a parsear texto libre. Con
``response_format={"type": "json_schema", "strict": true}`` el proveedor obliga
a la forma exacta, así que validamos con Pydantic en vez de con regex.

Por qué caché: el gate se paga por imagen. La clave es ``sha1`` de los bytes
del PNG + del prompt, así que re-correr el pipeline con el mismo PDF no
cuesta nada y cambiar el prompt invalida la caché sola.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from openai import AsyncOpenAI
from PIL import Image
from pydantic import BaseModel, Field, field_validator

try:
    from .image_manifest import ImageCandidate
    from .prompt_vision import (
        GATE_KINDS,
        GATE_SCHEMA,
        GATE_SYSTEM_PROMPT,
        GATE_USER_PROMPT,
    )
except ImportError:
    from image_manifest import ImageCandidate
    from prompt_vision import GATE_KINDS, GATE_SCHEMA, GATE_SYSTEM_PROMPT, GATE_USER_PROMPT

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Modelo del gate. Medido sobre una figura real (1506x1025 px):
#   gpt-5-nano  + reasoning_effort=minimal -> $0.000093, pero llama
#       "graphical_abstract" a un plot (el mismo modelo CON razonamiento lo
#       llama "plot"). Ese sesgo va en la direccion peligrosa: una tabla WVTR
#       rasterizada mirada como "graphical abstract" -> contains_wvtr=false ->
#       falso negativo irreversible.
#   gemini-2.5-flash-lite                -> $0.000106, clasifica bien, 0 tokens
#       de razonamiento. Mismo precio, mucha mas precision: gana.
GATE_MODEL = "google/gemini-2.5-flash-lite"   # $0.10 / $0.40 por 1M tokens
GATE_CACHE_DIR = Path(os.environ.get("WVTR_DATA_DIR", Path.cwd() / "temp")) / "image_gate"
GATE_MAX_TOKENS = 300
GATE_MAX_RETRIES = 3
GATE_BASE_DELAY = 5

#: Ancho máximo (px) al que se reescala la imagen antes de enviarla.
#: OpenRouter/los proveedores ignoran ``detail: "low"``: con la imagen completa
#: el prompt costaba 2971 tokens en vez de 743. Redimensionar con Pillow es la
#: única forma determinista de controlar el coste, y es independiente del
#: proveedor. El gate solo necesita distinguir tabla vs. gráfica, no leer
#: dígitos, así que 768 px no pierde información útil.
GATE_MAX_IMAGE_WIDTH_PX = 768

#: kind -> ¿tiene sentido pedirle extracción?
EXTRACTABLE_KINDS = {"data_table"}


class GateError(Exception):
    pass


class ImageVerdict(BaseModel):
    """Veredicto del gate. Validado por Pydantic: si no encaja, no se usa."""

    kind: str = Field(description="Debe ser uno de GATE_KINDS.")
    contains_wvtr: bool
    table_readable: bool
    mentions_wvtr_terms: bool
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = ""

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        # El enum del json_schema ya lo impone el proveedor, pero si un endpoint
        # degradara a texto libre este chequeo evita que un kind inventado se
        # cuele y termine en EXTRACTABLE_KINDS.
        if value not in GATE_KINDS:
            raise ValueError(f"kind desconocido: {value!r}; esperado uno de {GATE_KINDS}")
        return value


@dataclass
class GateResult:
    candidate: ImageCandidate
    verdict: ImageVerdict
    cached: bool = False
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def should_extract(self) -> bool:
        """¿Manda al extractor?

        Las tres condiciones son necesarias: la imagen debe traer WVTR impreso,
        los valores deben ser legibles y debe ser una tabla (no una gráfica —
        decisión de proyecto: los plots no se digitalizan).
        """
        v = self.verdict
        return bool(
            v.contains_wvtr
            and v.table_readable
            and v.kind in EXTRACTABLE_KINDS
        )

    def to_dict(self) -> dict:
        return {
            "name": self.candidate.name,
            "page": self.candidate.page,
            "model_verdict": self.verdict.model_dump(),
            "should_extract": self.should_extract,
            "cached": self.cached,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }


# ─────────────────────────────── caché ───────────────────────────────

def _cache_key(cand: ImageCandidate, model: str) -> str:
    digest = hashlib.sha1()
    digest.update(model.encode())
    digest.update(GATE_SYSTEM_PROMPT.encode())
    try:
        digest.update(Path(cand.path).read_bytes())
    except OSError:
        digest.update(cand.name.encode())
    return f"{cand.stem}__{digest.hexdigest()[:16]}"


def _cache_load(key: str) -> ImageVerdict | None:
    path = GATE_CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        return ImageVerdict.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _cache_store(key: str, verdict: ImageVerdict):
    GATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (GATE_CACHE_DIR / f"{key}.json").write_text(
        json.dumps(verdict.model_dump(), ensure_ascii=False, indent=1), encoding="utf-8"
    )


# ─────────────────────────────── gate ───────────────────────────────

def prepare_image(cand: ImageCandidate, max_width: int = GATE_MAX_IMAGE_WIDTH_PX) -> tuple[str, tuple[int, int]]:
    """Data URI base64 con la imagen reescalada a ``max_width`` px de ancho.

    Returns:
        (data_uri, (ancho_original, ancho_enviado))
    """
    raw = Path(cand.path).read_bytes()
    original_w = 0
    if Image is not None:
        try:
            with Image.open(BytesIO(raw)) as im:
                original_w = im.width
                if im.width > max_width:
                    resized = im.convert("RGB").resize(
                        (max_width, max(1, round(im.height * max_width / im.width))),
                        Image.LANCZOS,
                    )
                    buf = BytesIO()
                    resized.save(buf, format="JPEG", quality=85)
                    raw = buf.getvalue()
                    return (
                        "data:image/jpeg;base64," + base64.b64encode(raw).decode(),
                        (original_w, max_width),
                    )
        except Exception:
            pass
    return "data:image/png;base64," + base64.b64encode(raw).decode(), (original_w, 0)


def encode_image(cand: ImageCandidate) -> str:
    """Data URI base64 sin reescalar (ruta de extracción, donde sí se leen dígitos)."""
    data = Path(cand.path).read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode()


async def classify_image(
    client: AsyncOpenAI,
    cand: ImageCandidate,
    doi: str = "",
    model: str = GATE_MODEL,
) -> GateResult:
    """Clasifica UNA imagen. Lanza :class:`GateError` si el modelo no responde."""
    key = _cache_key(cand, model)
    cached = _cache_load(key)
    if cached is not None:
        return GateResult(candidate=cand, verdict=cached, cached=True)

    data_uri, (orig_w, sent_w) = prepare_image(cand)

    payload = [
        {"type": "text", "text": GATE_USER_PROMPT.format(
            doi=doi or "N/A",
            image_name=cand.name,
            page=cand.page,
            context=(cand.context or "(no surrounding text)")[:1200],
        )},
        {"type": "image_url", "image_url": {"url": data_uri, "detail": "low"}},
    ]

    last_error = None
    for attempt in range(GATE_MAX_RETRIES):
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": GATE_SYSTEM_PROMPT},
                        {"role": "user", "content": payload},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": GATE_SCHEMA,
                    },
                    max_tokens=GATE_MAX_TOKENS,
                    # OpenRouter enruta por defecto a endpoints que ignoran
                    # parámetros desconocidos; con require_parameters=True solo
                    # usa endpoints que respetan response_format. Sin esto, un
                    # proveedor sin structured outputs devolvería texto libre y
                    # el ImageVerdict de Pydantic reventaría.
                    extra_body={"provider": {"require_parameters": True}},
                ),
                timeout=60,
            )
            break
        except TypeError as e:
            # Firma incorrecta: no es transitorio, no tiene sentido reintentar.
            raise GateError(f"Llamada invalida al LLM en {cand.name}: {e}") from e
        except Exception as e:  # noqa: BLE001 — se reintenta y se clasifica abajo
            last_error = str(e)
            if attempt < GATE_MAX_RETRIES - 1:
                delay = GATE_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "[GATE RETRY %d/%d] %s: %s (espera %ds)",
                    attempt + 1, GATE_MAX_RETRIES, cand.name, last_error, delay,
                )
                await asyncio.sleep(delay)
    else:
        raise GateError(f"Gate fallo tras {GATE_MAX_RETRIES} intentos en {cand.name}: {last_error}")

    raw = response.choices[0].message.content
    usage = getattr(response, "usage", None)
    tokens_in = getattr(usage, "prompt_tokens", 0) or 0
    tokens_out = getattr(usage, "completion_tokens", 0) or 0

    try:
        verdict = ImageVerdict.model_validate_json(raw)
    except Exception as e:  # noqa: BLE001
        raise GateError(f"Veredicto invalido de {cand.name}: {e} | raw={raw[:300]}") from e

    _cache_store(key, verdict)
    logger.info(
        "[GATE] %s (p.%s, %dpx->%dpx) kind=%s wvtr=%s legible=%s conf=%.2f -> %s",
        cand.name, cand.page, orig_w, sent_w, verdict.kind, verdict.contains_wvtr,
        verdict.table_readable, verdict.confidence,
        "EXTRAER" if (verdict.contains_wvtr and verdict.table_readable
                      and verdict.kind in EXTRACTABLE_KINDS) else "descartar",
    )
    return GateResult(candidate=cand, verdict=verdict, tokens_in=tokens_in, tokens_out=tokens_out)


async def gate_all(
    client: AsyncOpenAI,
    candidates: list[ImageCandidate],
    doi: str = "",
    model: str = GATE_MODEL,
    concurrency: int = 10,
) -> tuple[list[GateResult], list[GateResult], dict]:
    """Clasifica todas las candidatas. Devuelve (resultados, fallos, stats)."""
    semaphore = asyncio.Semaphore(concurrency)
    stats = {"gate_tokens_in": 0, "gate_tokens_out": 0, "gate_calls": 0, "gate_cached": 0}

    async def one(cand: ImageCandidate) -> GateResult:
        async with semaphore:
            res = await classify_image(client, cand, doi, model)
            stats["gate_tokens_in"] += res.tokens_in
            stats["gate_tokens_out"] += res.tokens_out
            if res.cached:
                stats["gate_cached"] += 1
            else:
                stats["gate_calls"] += 1
            return res

    settled = await asyncio.gather(*(one(c) for c in candidates), return_exceptions=True)

    results, failures = [], []
    for cand, item in zip(candidates, settled):
        if isinstance(item, GateError):
            logger.error("[GATE FAIL] %s: %s", cand.name, item)
            failures.append(item)
        elif isinstance(item, Exception):  # noqa: BLE001
            logger.error("[GATE ERROR] %s: %s", cand.name, item)
            failures.append(item)
        else:
            results.append(item)

    stats["gate_to_extract"] = sum(1 for r in results if r.should_extract)
    return results, failures, stats


def summarize_gates(results: list[GateResult]) -> dict:
    """Recuento por kind para el log: revela si el gate está sesgado."""
    by_kind: dict[str, int] = {}
    for r in results:
        by_kind[r.verdict.kind] = by_kind.get(r.verdict.kind, 0) + 1
    return {
        "gate_kinds": by_kind,
        "gate_wvtr_hits": sum(1 for r in results if r.verdict.contains_wvtr),
        "gate_to_extract": sum(1 for r in results if r.should_extract),
    }


def gate_cost_estimate(stats: dict, in_price: float = 0.05, out_price: float = 0.40) -> float:
    """USD gastado por el gate de un documento."""
    return (stats["gate_tokens_in"] * in_price + stats["gate_tokens_out"] * out_price) / 1e6
