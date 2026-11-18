"""prompt_vision.py — Prompts y JSON Schemas para la ruta de imágenes.

Dos etapas con dos modelos distintos:

1. ``GATE_*``  — un modelo barato y SIN razonamiento solo responde
   ``¿esta imagen contieneWVTR?``. Salida de ~40 tokens,constrained por
   ``json_schema`` estricto: no puede inventar campos ni devolver texto libre.
   Coste ~$0.00006 por imagen.

2. ``VISION_*`` — GPT-5.6 Luna lee la imagen y devuelve los registros, con el
   mismo esquema de campos que la ruta de texto.

Sobre los JSON Schemas: OpenRouter expone ``response_format`` /
``structured_outputs``. En modo ``strict`` el proveedor obliga a que la
respuesta cumpla el esquema, así que ``parse_llm_json`` deja de ser la
defensa principal (se conserva como red de seguridad). Restricciones de este
formato que el código respeta: *todas* las claves en ``required``,
``additionalProperties: false``, sin campos opcionales y sin keywords como
``minLength``/``minimum`` (varies por proveedor).
"""

from __future__ import annotations

# ─────────────────────────── ETAPA 1: GATE ───────────────────────────

GATE_KINDS = [
    "data_table",        # tabla con valores numéricos impresos
    "plot",              # gráfica de barras/líneas/dispersión
    "photo_micrograph",  # micrografía óptica o SEM
    "schematic",         # estructura química, diagrama, esquema
    "graphical_abstract",
    "logo",
    "decorative",
    "equation",
    "other",
]

WVTR_TERMS = (
    "WVTR, WVR, water vapor (or vapour) transmission (rate), water vapor "
    "permeability (WVP), permeance, moisture barrier, humidity permeability"
)

GATE_SYSTEM_PROMPT = """You are a binary triage filter for a scientific document extraction pipeline. You inspect ONE extracted image from a polymer-science article and decide whether it is worth sending to an expensive extractor model.

You do NOT extract data. You only classify.

## What counts as WVTR data
Only text explicitly printed IN THE IMAGE qualifies. Relevant: {terms}
and units such as g/m2/day, g.mm/(m2.day), g/(m2*24h), cc/(m2*24h).

## Rules

1. `contains_wvtr` is true ONLY if the image itself shows WVTR / WVP values or a table/plot whose row/axis labels refer to them. A plot whose axis says "Young's modulus" or "DSC traces" is `contains_wvtr: false` even though the article discusses barrier properties elsewhere.
2. The surrounding article text is context, NOT evidence. Text near the image does not make the image relevant. Judge the image.
3. Publisher furniture is `logo` or `decorative` with `contains_wvtr: false`, regardless of what the article is about.
4. `table_readable` is true only when individual cell values are legible as printed characters. A low-resolution or overlapping table is `contains_wvtr: true` but `table_readable: false`, and will be skipped by the extractor.
5. `kind: plot` means the values live only in geometry (bar height, line position). Those are NOT reliably extractable, so mark `table_readable: false`.
6. `confidence` is your calibrated belief in `contains_wvtr`, 0.0 to 1.0. Say 0.5 when you genuinely cannot tell.
7. `evidence` is one short clause (<=20 words) quoting what you actually saw.

Answer only with the JSON object required by the schema.""".format(terms=WVTR_TERMS)

GATE_SCHEMA = {
    "name": "image_wvtr_verdict",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": GATE_KINDS,
                "description": "What the image shows.",
            },
            "contains_wvtr": {
                "type": "boolean",
                "description": "True only if the image itself prints WVTR/WVP values.",
            },
            "table_readable": {
                "type": "boolean",
                "description": "True only if printed cell/label values are legible.",
            },
            "mentions_wvtr_terms": {
                "type": "boolean",
                "description": "True if the words WVTR/WVP/water vapor appear in the image.",
            },
            "confidence": {
                "type": "number",
                "description": "Calibrated confidence in contains_wvtr, 0.0 to 1.0.",
            },
            "evidence": {
                "type": "string",
                "description": "Max 20 words quoting what was seen in the image.",
            },
        },
        "required": [
            "kind",
            "contains_wvtr",
            "table_readable",
            "mentions_wvtr_terms",
            "confidence",
            "evidence",
        ],
        "additionalProperties": False,
    },
}

GATE_USER_PROMPT = """Article: {doi}
Image file: {image_name} (page {page})

Surrounding text from the article (context only, NOT evidence):
---
{context}
---

Classify the image above."""


# ─────────────────────── ETAPA 2: EXTRACCIÓN ───────────────────────

# Reglas heredadas de prompt.py (NO INFERENCE, UNITS verbatim, carry-down de
# celdas combinadas, una fila = una medición) más las específicas de imagen.
_VISION_RULES = """
## Rules

1. NO INFERENCE, NO COMPLETION: Extract only values you can literally read in the image. Do not calculate, estimate, interpolate, or infer from context, from what is typical for the polymer, or from other tables. If a condition (temperature, RH, thickness, test method) is not printed for a given value, set that field to null.

2. GRAPHS ARE NOT A DATA SOURCE: if the image is a bar chart, line plot, scatter plot or any figure where values are encoded as geometry (bar height, axis position, marker placement), you MUST return an empty `registros` list. Reading a value off an axis is an estimate, not a measurement, and this pipeline forbids estimates.

3. UNITS: report `wvtr_units` exactly as printed in the image (e.g. "g/m2/day", "g.mm/m2.day"). Do NOT normalize or convert. A second downstream step handles normalization.

4. TABLES: one row = one measurement. If a polymer name or a condition is printed once and applies to several rows (merged/spanned cell, or stated in a header line), carry it down explicitly to each row. Do not merge values across rows or columns.

5. SAMPLE CODES: if the table uses codes ("PLA-2", "PBAT50", "Sample B") that map to a polymer defined in the surrounding text, resolve the mapping and record BOTH the code and the resolved polymer name, joined with " (e.g. "PBAT50 (PBAT)").

6. MULTIPLE CONDITIONS PER POLYMER: a polymer measured under several conditions yields several records. Never average and never drop a row as redundant.

7. TEST METHOD: report the standard exactly as printed (e.g. "ASTM F1249", "ISO 2528"). If not printed, null.

8. PARTIAL TABLES: if only some rows are legible, extract only those rows. Do not guess the rest.

9. NO DATA: if this image contains no WVTR measurement, return an empty `registros` list. Never fabricate placeholder entries.

10. PROVENANCE: set `source_ref` to the image file name given in the prompt and `source_page` to the page number given in the prompt. Do not invent them.
"""

VISION_SYSTEM_PROMPT = (
    """You are a scientific data extraction engine specialized in polymer science literature. Your sole function is to extract Water Vapor Transmission Rate (WVTR) measurements and their associated experimental conditions from a SINGLE IMAGE taken from an article (usually a rasterized table), and return them as structured JSON conforming exactly to the provided schema."""
    + _VISION_RULES
    + """
## JSON Schema

```json
{
  "article_title": "string - title of the article, or null if unknown from the image",
  "registros": [
    {
      "polymer": "string",
      "wvtr_value": "number",
      "wvtr_units": "string",
      "temperature": "string|null",
      "rh": "string|null",
      "thickness": "string|null",
      "test_method": "string|null",
      "source_ref": "string - image file name",
      "source_page": "number - 1-based page number"
    }
  ]
}
```

Answer only with the JSON object required by the schema."""
)

VISION_SCHEMA = {
    "name": "wvtr_from_image",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "article_title": {
                "type": "string",
                "description": "Article title if visible in the image, else empty string.",
            },
            "registros": {
                "type": "array",
                "description": "One entry per measurement row; empty if none.",
                "items": {
                    "type": "object",
                    "properties": {
                        "polymer": {"type": "string", "description": "Polymer name."},
                        "wvtr_value": {"type": "number", "description": "WVTR value as printed."},
                        "wvtr_units": {"type": "string", "description": "Units exactly as printed."},
                        "temperature": {"type": "string", "description": "Test temperature or empty string."},
                        "rh": {"type": "string", "description": "Relative humidity or empty string."},
                        "thickness": {"type": "string", "description": "Film thickness or empty string."},
                        "test_method": {"type": "string", "description": "ASTM/ISO standard or empty string."},
                        "source_ref": {"type": "string", "description": "Image file name."},
                        "source_page": {"type": "number", "description": "1-based page."},
                    },
                    "required": [
                        "polymer",
                        "wvtr_value",
                        "wvtr_units",
                        "temperature",
                        "rh",
                        "thickness",
                        "test_method",
                        "source_ref",
                        "source_page",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["article_title", "registros"],
        "additionalProperties": False,
    },
}

VISION_USER_PROMPT = """DOI: {doi}
Image file: {image_name} (page {page})

Surrounding text from the article. Use it ONLY to resolve sample codes and
abbreviations. It is not a source of measurements.
---
{context}
---

Extract the WVTR measurements visible in this image."""
