"""image_filter.py — Descarte heurístico de imágenes, sin costo ni red.

Por qué una etapa gratis antes del gate: en un paper real de Springer, 38 de
las imágenes extraídas eran 23 logos/oramentos de portada y caja del editor, y
solo ~14 eran figuras. Cada imagen descartada aquí es ~1.000 tokens de visión
que no se envían. Medido con
``temp/markdown/images/10.1007_s10924-018-1256-x``:

   logos    máx 25 pt x 23 pt   (≤105 px de ancho, ≤23 colores)
   figuras  mín 113 pt x 60 pt

**Filosofía: preservar recall.** El gate cuesta ~$0.00006 por imagen, así que
descartar una tabla válida cuesta dinero real y un falso negativo, mientras que
mandar un logo al gate cuesta centavos. Cada regla está escrita para descartar
solo lo *inequívocamente* irrelevante:

- ``tiny_px``      : la señal más fuerte (los logos miden 2–105 px de ancho).
- ``tiny_pt``      : solo si **ambas** dimensiones son diminutas. Con "o" se
                     mataba un válido ``imageFile26`` de 312x47 pt.
- ``strip``        : aspecto extremo Y área despreciable (un filete a 6:1 sí es
                     decorativo; una tabla panorámica de 6:1 no).
- ``band``         : header/footer Y área pequeña (las figuras de ancho completo
                     llegan hasta el 7 % del alto de página).
- ``few_colors``   : monocroma Y pequeña (un logo; una tabla siempre tiene texto
                     y líneas de color).

Con el paper de prueba: 38 → 14 candidatas, 0 Potenciales tablas perdidas.
"""

from __future__ import annotations

from dataclasses import dataclass

try:
    from .image_manifest import ImageCandidate
except ImportError:
    from image_manifest import ImageCandidate


class FilterError(Exception):
    pass


@dataclass(frozen=True)
class FilterConfig:
    """Umbrales de descarte. Calibrados sobre paper real; ajustables sin tocar código."""

    min_width_px: int = 150
    min_height_px: int = 100
    min_width_pt: float = 70.0        # logos observados: ≤25 pt
    min_height_pt: float = 50.0       # logos observados: ≤23 pt
    strip_aspect: float = 8.0         # con area_ratio < strip_max_area_ratio
    strip_max_area_ratio: float = 0.01
    band_max_area_ratio: float = 0.10  # header/footer Y menor que esto
    header_band: float = 0.06
    footer_band: float = 0.94
    logo_max_colors: int = 24
    logo_max_area_ratio: float = 0.05


DEFAULT_CONFIG = FilterConfig()


def heuristic_reason(cand: ImageCandidate, cfg: FilterConfig = DEFAULT_CONFIG) -> str:
    """Devuelve el motivo del descarte, o ``""`` si la imagen es candidata."""
    # 1) Píxeles: señal más fuerte e independiente de la geometría del PDF.
    if cand.width_px and cand.height_px:
        if cand.width_px < cfg.min_width_px or cand.height_px < cfg.min_height_px:
            return f"tiny_px({cand.width_px}x{cand.height_px})"

    # 2) Puntos: ambas dimensiones deben ser diminutas.
    if cand.width_pt and cand.height_pt:
        if cand.width_pt < cfg.min_width_pt and cand.height_pt < cfg.min_height_pt:
            return f"tiny_pt({cand.width_pt:.0f}x{cand.height_pt:.0f})"

    # 3-5) Reglas que solo aplican con geometría de página conocida.
    if cand.has_page_size:
        if cand.aspect > cfg.strip_aspect and cand.area_ratio < cfg.strip_max_area_ratio:
            return f"strip(aspect={cand.aspect:.1f},area={cand.area_ratio * 100:.2f}%)"
        if cand.area_ratio < cfg.strip_max_area_ratio and cand.width_pt < cfg.min_width_pt * 2:
            return f"tiny_area({cand.area_ratio * 100:.2f}%_page)"
        in_band = cand.pos_top_ratio < cfg.header_band or cand.pos_top_ratio > cfg.footer_band
        if in_band and cand.area_ratio < cfg.band_max_area_ratio:
            return f"band({cand.pos_top_ratio * 100:.0f}%,{cand.area_ratio * 100:.1f}%)"
        if (
            0 < cand.unique_colors <= cfg.logo_max_colors
            and cand.area_ratio <= cfg.logo_max_area_ratio
        ):
            return f"few_colors({cand.unique_colors})"

    return ""


def filter_manifest(
    manifest: list[ImageCandidate], cfg: FilterConfig = DEFAULT_CONFIG
) -> tuple[list[ImageCandidate], dict[str, int]]:
    """Particiona el manifiesto en (candidatas, reasons).

    ``reasons`` es un contador ``{motivo: n}`` para auditar en el log cuántos
    tokens se ahorró y con qué regla.
    """
    candidates: list[ImageCandidate] = []
    reasons: dict[str, int] = {}
    for cand in manifest:
        reason = heuristic_reason(cand, cfg)
        if reason:
            key = reason.split("(")[0]
            reasons[key] = reasons.get(key, 0) + 1
        else:
            candidates.append(cand)
    return candidates, reasons


def summarize(manifest: list[ImageCandidate]) -> dict:
    """Métricas del manifiesto para el log de la Fase 2."""
    return {
        "images_total": len(manifest),
        "bytes_total": sum(c.bytes for c in manifest),
        "with_page_size": sum(1 for c in manifest if c.has_page_size),
        "with_context": sum(1 for c in manifest if c.context),
        "with_alt": sum(1 for c in manifest if c.alt),
    }
