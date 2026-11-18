"""reconcile.py — Fusión de los registros de texto con los de imagen.

El problema que resuelve: una tabla WVTR rasterizada puede aparecer **también**
en el flujo de texto (si opendataloader logró vectorizarla como HTML, o si los
píxeles tienen capa de texto). Sin reconciliación, el mismo valor entra dos
veces y ``replace_document`` inserta filas duplicadas: la base de datos deja de
ser fiable para meta-análisis.

Estrategia: una identidad por *medición*, no por fila.

    identidad = (polymer normalizado, valor normalizado, unidades normalizadas)

Se eligen esos tres campos porque son los que definen qué se midió. La
temperatura, RH, espesor y método **no** forman parte de la identidad a
propósito: son metadatos que una de las dos rutas puede tener más completos, y
unificarlos en vez de duplicarlos es lo correcto.

Reglas de fusión, en orden:
1. Si la imagen y el texto coinciden en identidad → una sola fila, se conserva
   la de texto (más fiable: no hay OCR de por medio), se rellenan sus campos
   vacíos con los de la imagen, y se marca ``corroborated``.
2. Si solo hay imagen → entra con ``source_kind='image'``.
3. Duplicados dentro de la misma ruta → se colapsan (una tabla leída dos veces).
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

TEXT = "text"
IMAGE = "image"
BOTH = "text+image"

_FIELDS = ("polymer", "wvtr_value", "wvtr_units", "temperature", "rh", "thickness",
           "test_method")


class ReconcileError(Exception):
    pass


def _norm_text(value) -> str:
    """Minúsculas, sin acentos, sin separadores ni unidades pegadas."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9.]+", "", text.lower())


def _norm_polymer(value) -> str:
    """Idéntico a ``_norm_text`` pero sin puntos: 'PBAT-50' y 'PBAT50' colisionan."""
    return _norm_text(value).replace(".", "")


def _to_float(raw: str):
    """Convierte el número impreso en una tabla a float. ``None`` si no lo es.

    Gestiona lo que aparece de verdad en tablas de papers:
      - separador de miles: "1,234.5" (en), "1.234,5" (eu), "1 234,5" (fr)
      - decimal coma: "4,5"
      - coma y punto juntos: el último es el decimal, el otro es de miles
      - notación científica: "4.5e-3"
      - unidades pegadas al valor: "4.5 g/m2/day"
    """
    text = raw.strip().replace("\u00a0", " ").replace("\u2212", "-")
    # Tolerancia inicial: los papers imprimen "~4.2", ">1000", "<0.1".
    match = re.match(
        r"^[~≈<>=≤≥\s]*[-+]?\s*\d(?:[\d.,\s]*\d)?(?:[eE][-+]?\d+)?", text
    )
    if not match:
        return None
    token = match.group(0).replace(" ", "").replace("\u00a0", "")
    # Se quitan los símbolos de tolerancia del inicio para poder validar el token.
    token = re.sub(r"^[~≈<>=≤≥]+", "", token)
    if not re.match(r"^[-+]?\d", token):
        return None

    last_dot, last_comma = token.rfind("."), token.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        # Ambos: el que aparece más a la derecha es el decimal.
        decimal = "." if last_dot > last_comma else ","
        thousands = "," if decimal == "." else "."
        token = token.replace(thousands, "")
        if decimal == ",":
            token = token.replace(",", ".")
    else:
        sep = "." if last_dot >= 0 else ("," if last_comma >= 0 else None)
        if sep:
            head, _, tail = token.partition(sep)
            # Separador de miles = parte entera no nula + exactamente 3 dígitos.
            # "1,234" -> 1234 pero "0.045" -> 0.045 (un 0 inicial delata decimal).
            is_thousands = head not in ("", "0", "-0", "+0") and len(tail) == 3 and tail.isdigit()
            token = head + tail if is_thousands else head + "." + tail

    try:
        return float(token)
    except ValueError:
        return None


def _norm_value(value):
    """4 cifras significativas, para que 4.5, 4.50 y "4,5" sean el mismo número."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        number = _to_float(str(value))
        if number is None:
            return None
    if number == 0:
        return 0.0
    return round(number, 3 - math.floor(math.log10(abs(number))))


def identity(record: dict) -> tuple:
    """Clave de identidad de una medición."""
    return (
        _norm_polymer(record.get("polymer")),
        _norm_value(record.get("wvtr_value")),
        _norm_text(record.get("wvtr_units")),
    )


@dataclass
class ReconcileStats:
    text_records: int = 0
    image_records: int = 0
    duplicates_dropped: int = 0
    corroborated: int = 0
    fields_filled_from_image: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _richness(record: dict) -> int:
    return sum(1 for f in _FIELDS if f not in ("polymer", "wvtr_value")
               and record.get(f) not in (None, "", "null"))


def reconcile(text_records: list[dict], image_records: list[dict]) -> tuple[list[dict], ReconcileStats]:
    """Fusiona ambos conjuntos. Devuelve (registros, stats).

    Los registros devueltos llevan las claves de procedencia
    ``source_kind``/``source_ref``/``source_page``/``corroborated`` para que la
    Fase 3 pueda guardarlas en columnas dedicadas.
    """
    stats = ReconcileStats(text_records=len(text_records or []),
                           image_records=len(image_records or []))

    merged: dict[tuple, dict] = {}
    order: list[tuple] = []

    # 1) Texto: es la fuente de referencia (sin OCR de por medio).
    for record in text_records or []:
        key = identity(record)
        if key[0] == "" or key[1] is None:
            # Sin polímero o sin valor no hay medición identificable: se descarta.
            stats.duplicates_dropped += 1
            continue
        entry = dict(record)
        if key in merged:
            # Mismo valor listado dos veces en el texto: conserva el más rico.
            stats.duplicates_dropped += 1
            if _richness(entry) > _richness(merged[key]):
                merged[key] = {**merged[key], **entry}
            continue
        merged[key] = entry
        order.append(key)

    # 2) Imágenes.
    for record in image_records or []:
        key = identity(record)
        if key[0] == "" or key[1] is None:
            stats.duplicates_dropped += 1
            continue
        if key in merged:
            existing = merged[key]
            stats.corroborated += 1
            for field_name in _FIELDS:
                if existing.get(field_name) in (None, "", "null") and \
                        record.get(field_name) not in (None, "", "null"):
                    existing[field_name] = record[field_name]
                    stats.fields_filled_from_image += 1
            existing["source_kind"] = BOTH
            existing["corroborated"] = True
            # La referencia de la imagen se conserva aparte: es la evidencia de
            # que el dato también estaba en la figura/tabla.
            existing["source_ref_image"] = record.get("source_ref") or ""
            existing["source_page_image"] = record.get("source_page")
            continue
        entry = dict(record)
        entry["source_kind"] = IMAGE
        entry["corroborated"] = False
        merged[key] = entry
        order.append(key)

    out = []
    for key in order:
        entry = merged[key]
        entry.setdefault("source_kind", TEXT)
        entry.setdefault("corroborated", False)
        out.append(entry)

    return out, stats
