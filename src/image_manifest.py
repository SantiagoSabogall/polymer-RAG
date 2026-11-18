"""image_manifest.py — Inventario de imágenes extraídas de cada PDF.

Por qué existe: ``opendataloader-pdf`` con ``image_output="external"`` extrae
*todas* las imágenes del PDF (logos de editorial, filetes, iconos, Capability
Watermark, figuras, micrografías, gráficas y tablas rasterizadas). Enviar todas
al LLM sería tirar tokens. Este módulo construye un inventario **sin costo**
uniendo tres fuentes que el backend ya produce gratis:

1. ``<stem>.json`` (outline): por cada imagen da ``page number``, ``bounding
   box`` (PDF user space, ``[x0, y0, x1, y1]``, origen abajo-izquierda),
   ``source`` (nombre real del PNG), ``alt``, ``alt_source``, ``pdfua_tag``.
2. Los PNG en disco: dimensiones en píxeles, nº de colores, ratio de tinta
   (Pillow). Distingue un logo monocromo de una tabla densa.
3. El Markdown limpio: las líneas alrededor del ``![](…)`` dan el contexto
   textual (títulos de sección, frases que mencionan la tabla).

Geometría: el PDF se borra al final de la Fase 1, así que el tamaño de página
se captura ahí (``page_sizes``) y se guarda en el manifiesto. Si no está
disponible, los umbrales en puntos siguen funcionando (más toscos).

Cero red, cero tokens.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # Pillow es dependencia dura del filtro; si falta, se degrada
    Image = None


class ManifestError(Exception):
    pass


# Centinela para "la imagen tiene más de _COLOR_SATURATION colores distintos".
# Evita que getcolors() devolviendo None se interprete como imagen monocroma.
_COLOR_SATURATION = 4096
_MANY_COLORS = _COLOR_SATURATION + 1


# bbox del outline = [x0, y0, x1, y1] en PDF user space (origen abajo-izquierda)
_IMAGE_REF = re.compile(r"!\[[^\]]*\]\(<?([^)>]+)>?\)")


@dataclass
class ImageCandidate:
    """Una imagen extraída, con todo lo que sabemos sin gastar tokens."""

    stem: str
    name: str                       # imageFile24.png
    page: int = 0
    width_pt: float = 0.0
    height_pt: float = 0.0
    area_ratio: float = 0.0         # área de la imagen / área de la página (0..1)
    pos_top_ratio: float = 0.5      # 0 = borde superior de la página, 1 = inferior
    width_px: int = 0
    height_px: int = 0
    bytes: int = 0
    ink_ratio: float = 0.0          # fracción de píxeles no blancos
    unique_colors: int = 0
    alt: str = ""
    alt_source: str = ""
    pdfua_tag: str = ""
    context: str = ""               # texto alrededor del ![](…)
    path: str = ""                  # ruta absoluta en disco ("" si no existe)
    has_page_size: bool = False

    @property
    def aspect(self) -> float:
        if not self.height_pt:
            return 0.0
        return max(self.width_pt / self.height_pt, self.height_pt / self.width_pt)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["aspect"] = round(self.aspect, 3)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ImageCandidate":
        d.pop("aspect", None)
        return cls(**d)


# ───────────────────────────── salida a disco ──────────────────────────────

def manifest_path_for(md_path) -> Path:
    """Ruta del manifiesto cacheado para un Markdown dado."""
    return Path(md_path).parent / "manifest" / f"{Path(md_path).stem}.json"


def write_manifest(md_path, candidates: list[ImageCandidate]) -> Path:
    """Guarda el manifiesto como sidecar JSON (reutilizado por la Fase 2)."""
    out = manifest_path_for(md_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps([c.to_dict() for c in candidates], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return out


def load_manifest(md_path) -> list[ImageCandidate]:
    """Lee el manifiesto cacheado. Lista vacía si no existe o está corrupto."""
    p = manifest_path_for(md_path)
    if not p.exists():
        return []
    try:
        return [ImageCandidate.from_dict(d) for d in json.loads(p.read_text(encoding="utf-8"))]
    except (json.JSONDecodeError, TypeError):
        return []


# ───────────────────────────── tamaño de página ────────────────────────────

def page_sizes(pdf_path) -> dict[int, tuple[float, float]]:
    """{página: (ancho_pt, alto_pt)} leído del MediaBox con pypdf.

    Se invoca en la Fase 1, mientras el PDF sigue en disco. Devuelve ``{}``
    si pypdf no está o el PDF es ilegible: el pipeline sigue con umbrales
    absolutos en puntos.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return {}
    try:
        reader = PdfReader(str(pdf_path))
        return {
            i + 1: (round(float(p.mediabox.width), 2), round(float(p.mediabox.height), 2))
            for i, p in enumerate(reader.pages)
        }
    except Exception:
        return {}


# ───────────────────────────── parsing del outline ─────────────────────────

def iter_nodes(obj):
    """Recorre el outline JSON.flatten de nodos con 'type' y 'bounding box'."""
    if isinstance(obj, dict):
        if "type" in obj and "bounding box" in obj:
            yield obj
        for value in obj.values():
            yield from iter_nodes(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from iter_nodes(value)


def _context_window(md_text: str, image_name: str, radius: int = 3) -> str:
    """Texto alrededor de la referencia a la imagen en el Markdown."""
    if not md_text:
        return ""
    lines = md_text.splitlines()
    needle = Path(image_name).name
    for i, line in enumerate(lines):
        if needle in line and "![" in line:
            lo, hi = max(0, i - radius), min(len(lines), i + radius + 1)
            window = [ln.strip() for ln in lines[lo:hi] if ln.strip() and needle not in ln]
            return " | ".join(window)[:1200]
    return ""


def _png_stats(path: Path) -> dict:
    """Dimensiones, peso, ratio de tinta y nº de colores. Todo gratis."""
    out = {"width_px": 0, "height_px": 0, "bytes": 0, "ink_ratio": 0.0, "unique_colors": 0}
    try:
        out["bytes"] = path.stat().st_size
    except OSError:
        return out
    if Image is None:
        return out
    try:
        with Image.open(path) as im:
            out["width_px"], out["height_px"] = im.size
            colors = im.convert("RGB").getcolors(maxcolors=4096)
            # None = más de 4096 colores distintos; se registra el centinela
            # _MANY_COLORS para que "muchos colores" nunca se lea como "pocos".
            out["unique_colors"] = len(colors) if colors else _MANY_COLORS
            gray = im.convert("L")
            hist = gray.histogram()
            total = sum(hist) or 1
            out["ink_ratio"] = round(sum(hist[:245]) / total, 4)
    except Exception:
        pass
    return out


# ───────────────────────────── construcción ────────────────────────────────

def build_manifest(md_path, outline_path=None, images_dir=None, sizes=None) -> list[ImageCandidate]:
    """Construye el inventario de imágenes de un PDF ya convertido.

    Args:
        md_path: Markdown generado (define el ``stem``).
        outline_path: ``<stem>.json``; si se omite se deriva del ``md_path``.
        images_dir: directorio con los PNG; si se omite se deriva del ``md_path``.
        sizes: ``{página: (w, h)}`` de ``page_sizes()``; ``{}`` si no se tiene.

    Returns:
        Lista de :class:`ImageCandidate` ordenada por (página, área desc).
    """
    md_path = Path(md_path)
    outline_path = Path(outline_path) if outline_path else md_path.with_suffix(".json")
    images_dir = Path(images_dir) if images_dir else md_path.parent / "images" / md_path.stem
    sizes = sizes or {}

    if not outline_path.exists():
        return []

    try:
        outline = json.loads(outline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ManifestError(f"No se pudo leer el outline {outline_path}: {e}") from e

    md_text = ""
    try:
        md_text = md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass

    candidates: list[ImageCandidate] = []
    for node in iter_nodes(outline):
        if node.get("type") != "image":
            continue
        bbox = node.get("bounding box") or [0, 0, 0, 0]
        if len(bbox) != 4:
            continue
        x0, y0, x1, y1 = (float(v) for v in bbox)
        page = int(node.get("page number") or 0)
        name = Path(str(node.get("source") or "")).name or f"image_file_{len(candidates) + 1}.png"
        img_path = images_dir / name

        pw, ph = sizes.get(page, (0.0, 0.0))
        has_size = pw > 0 and ph > 0
        width_pt, height_pt = max(0.0, x1 - x0), max(0.0, y1 - y0)

        cand = ImageCandidate(
            stem=md_path.stem,
            name=name,
            page=page,
            width_pt=round(width_pt, 2),
            height_pt=round(height_pt, 2),
            area_ratio=round(width_pt * height_pt / (pw * ph), 5) if has_size else 0.0,
            # PDF user space tiene origen abajo-izquierda: el borde superior es page_h - y1
            pos_top_ratio=round((ph - y1) / ph, 4) if has_size else 0.5,
            alt=(node.get("alt") or "").strip(),
            alt_source=str(node.get("alt_source") or ""),
            pdfua_tag=str(node.get("pdfua_tag") or ""),
            context=_context_window(md_text, name),
            path=str(img_path) if img_path.exists() else "",
            has_page_size=has_size,
            **_png_stats(img_path),
        )
        candidates.append(cand)

    candidates.sort(key=lambda c: (c.page, -c.area_ratio))
    return candidates
