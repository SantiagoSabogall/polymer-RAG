# Ruta de imágenes en el pipeline WVTR

Documento de referencia de la implementación de la extracción de WVTR desde
imágenes (tablas rasterizadas, figuras) añadida al pipeline que antes solo
leía texto.

- **Fecha**: 2026-10-12
- **Rama**: working tree sobre `2dbe14e`
- **Estado**: implementado y verificado en local; **no commiteado**

---

## 1. Resumen ejecutivo

El pipeline original extraía WVTR solo del texto del Markdown. El problema:
las tablas WVTR se publican con frecuencia como **imagen**, no como texto, y
`opendataloader-pdf` solo convierte a texto las tablas vectoriales (HTML). Una
tabla rasterizada no producía **ningún** texto: el dato era invisible para el
sistema.

La solución añade cuatro etapas encadenadas entre la conversión y la
extracción, con dos modelos distintos para no gastar tokens innecesarios:

| Etapa | Pregunta que responde | Coste |
|-------|----------------------|--------|
| Manifiesto | ¿Qué imágenes hay y dónde están? | $0 (datos que el backend ya emitía) |
| Heurística | ¿Es obviamente no una tabla de datos? | $0 (Pillow) |
| Gate | ¿Imprime *realmente* valores WVTR? | ~$0.0002 / imagen |
| Extracción | ¿Cuáles son los valores? | ~$0.0009 / imagen |

Resultados verificados:

- **Control negativo** (paper real sin WVTR): 38 imágenes extraídas → 24
  descartadas gratis → 14 pasan el gate → **0 seleccionadas**. Las 14
  clasificadas correctamente, con evidencia textual verificable.
- **Control positivo** (tabla rasterizada con verdad terreno conocida):
  **5/5 filas extraídas con los 7 campos correctos**, incluido un estándar
  distinto (ISO 2528) en una de las filas.
- **Regresión de texto**: 29 registros en 7 papers reales, **0 campos
  alterados** por el nuevo código.
- **57 tests** (54 unitarios sin red + 3 de integración contra la API real).

---

## 2. El bug que había

El punto de partida tenía un cambio sin commitear en
`src/pdf_to_markdown.py:24`:

```python
image_output="on"      # ← INVÁLIDO
```

Verificado en `opendataloader_pdf/jar/opendataloader-pdf-cli.jar`, la clase
`Config` contiene `isValidImageOutput` y acepta **solo**:

```
off | embedded | external
```

Cualquier otro valor lanza `Unsupported image output mode '%s'`. Es decir: ese
cambio habría tumbado la Fase 1 en los 70 PDFs de R2. El documento de plan
previo (`images.txt`, sin commitear) proponía `"separate"`, que **tampoco**
existe.

Corrección aplicada: `"external"`, que escribe los PNG como archivos sueltos y
deja en el Markdown una referencia `![alt](...)` — exactamente lo que la ruta
de imágenes necesita para recortar el contexto de cada figura.

---

## 3. Flujo: antes vs. ahora

### Antes

```
R2 ──► descargar PDF
    ──► pdf_to_markdown()                 image_output="off"
    ──► clean_markdown()                  regex sobre el texto
    ──► build manifest                    (no existía)
    ──► extract_wvtr()                    LECTURA DE TEXTO
    ──► parse JSON
    ──► PostgreSQL
```

### Ahora

```
R2 ──► descargar PDF
    ──► pdf_to_markdown(with_images=True) ─┬─► <stem>.md        (igual que antes)
                                          ├─► <stem>.json      outline: metadatos
                                          └─► images/<stem>/   imageFileN.png
    ──► clean_markdown()                  regex sobre el texto (sin cambios)
    ──► build_manifest(sizes=page_sizes)  ◄── NUEVO  geometría + stats de los PNG
    ──► extract_wvtr()                    LECTURA DE TEXTO (sin cambios)
    ──► filter_manifest()                  ◄── NUEVO  descarta logos, gratis
    ──► gate_all()                         ◄── NUEVO  Gemini flash-lite
    ──► extract_all()                      ◄── NUEVO  Luna, detail=high
    ──► reconcile(texto, imagen)           ◄── NUEVO  dedup + procedencia
    ──► PostgreSQL + columnas de procedencia
```

### Dos decisiones de ubicación que importan

**a) El manifiesto se construye en la Fase 1, no en la Fase 2.**
El tamaño de página (necesario para `area_ratio` y las bandas header/footer)
solo se puede leer del PDF, y el PDF se borra en el `finally` de
`process_single_pdf`. Capturar la geometría ahí evita tener que conservar el
archivo.

**b) El gate vive dentro de `process_file`, no como fase 1.5.**
Así comparte el semáforo, el cliente, el skip incremental y el log de
progreso ya resueltos, y `--phase 1|2|3|all` sigue funcionando igual.

---

## 4. Módulos nuevos

### 4.1 `src/image_manifest.py` — 265 líneas

**Qué decide**: qué imágenes existen, dónde, y cuánto pesan.

**Por qué**: el backend ya emitía un `outline.json` con la información
geométrica de cada imagen. Nadie lo estaba usando. Convierte ese archivo en un
inventario consultable, **sin gastar un solo token**.

| Función / clase | Responsabilidad |
|-----------------|-----------------|
| `ImageCandidate` | dataclass: página, bbox, dims px/pt, área relativa, tinta, colores, alt, contexto |
| `page_sizes(pdf_path)` | Lee el MediaBox con pypdf → `{página: (w, h)}`. Se llama en Fase 1 mientras el PDF existe |
| `iter_nodes(obj)` | Recorre el outline aplanando nodos con `type` + `bounding box` |
| `build_manifest(...)` | Une outline + PNG + Markdown → `list[ImageCandidate]` |
| `write_manifest` / `load_manifest` | Sidecar JSON en `temp/markdown/manifest/<stem>.json` |
| `manifest_path_for(md_path)` | Ruta del sidecar |
| `_context_window(md_text, name)` | ±3 líneas del Markdown alrededor del `![](…)` |
| `_png_stats(path)` | Dims, peso, nº de colores, ratio de tinta vía Pillow |

**Dos trampas reales del formato de salida**:

1. El bbox es `[x0, y0, x1, y1]`, **no** `[x, y, w, h]`. Mi primer script de
   análisis restó `x1 - y0` y produjo áreas de −144.000.000 %. Está cubierto
   por `test_bbox_parsed_as_x0y0x1y1`.
2. El PDF user space tiene el origen **abajo-izquierda**. Una imagen pegada
   al borde superior de la página tiene `pos_top_ratio ≈ 0`, que se calcula
   como `(page_h - y1) / page_h`.

**Por qué existe `area_ratio` y no solo el tamaño absoluto**: permite
comparar la figura con la página, no entre sí. Un logo de 100×100 px es
irrelevante en una carta y podría ser media figura en una página A5.

### 4.2 `src/image_filter.py` — 122 líneas

**Qué decide**: qué es tan obviamente irrelevante que ni siquiera merece una
llamada al gate.

**Por qué**: en el paper de prueba, 23 de las 38 imágenes eran logos de la
editorial y filetes de la caja del editor. Cada una descartada son ~2.000
tokens de visión que no se envían.

| Función / clase | Responsabilidad |
|-----------------|-----------------|
| `FilterConfig` | Umbrales en un solo sitio, ajustables sin tocar código |
| `heuristic_reason(cand, cfg)` | Devuelve el motivo del descarte, o `""` si pasa |
| `filter_manifest(manifest, cfg)` | `(candidatas, {motivo: n})` |
| `summarize(manifest)` | Métricas para el log |

**Filosofía declarada: preservar recall.** El gate cuesta $0.0002 por
imagen, así que mandar un logo al gate cuesta centavos, mientras que
descartar una tabla válida cuesta un dato que no vuelve. Por eso las reglas
están escritas para eliminar solo lo inequívoco.

Medición que forzó dos reescrituras del filtro:

| Regla | Primera versión | Problema detectado | Versión final |
|-------|-----------------|--------------------|--------------|
| Tamaño en puntos | `w < 70 OR h < 50` | Mataba `imageFile26` (312×47 pt), una tabla válida | `w < 70 AND h < 50` |
| Área relativa | `< 2 % de la página` | Mataba `imageFile25`, tabla inline de 1.5 % | `< 1 %` y ancho < 140 pt |
| Aspecto | `> 6` | Una tabla panorámica de 6:1 se descarta | `> 8` **y** área < 1 % |
| Banda header | siempre | Las figuras de ancho completo llegan al 7 % del alto | banda **y** área < 10 % |

`imageFile25` se verificó visualmente: es la estructura química del PLA. El
descarte era correcto, pero por casualidad. La regla se relajó igualmente.

### 4.3 `src/prompt_vision.py` — 229 líneas

**Qué decide**: qué se le pide a cada modelo y con qué forma de respuesta.

**Por qué `json_schema` estricto y no texto libre**: el prompt original pedía
"Answer ONLY: yes or no". Los modelos responden `"Sí."`, `"yes\n"`, o un
párrafo justificando. Eso obliga a parsear texto libre con regex, que es
frágil. Con `response_format={"type": "json_schema", "strict": true}` el
proveedor obliga a la forma exacta y se valida con Pydantic.

| Constante | Contenido |
|-----------|-----------|
| `GATE_KINDS` | 9 categorías: `data_table`, `plot`, `photo_micrograph`, `schematic`, `graphical_abstract`, `logo`, `decorative`, `equation`, `other` |
| `WVTR_TERMS` | Sinónimos aceptados: WVTR, WVR, water vapor transmission, WVP, permeance, moisture barrier |
| `GATE_SYSTEM_PROMPT` | 7 reglas de clasificación |
| `GATE_SCHEMA` | 6 campos: `kind`, `contains_wvtr`, `table_readable`, `mentions_wvtr_terms`, `confidence`, `evidence` |
| `VISION_SYSTEM_PROMPT` | 10 reglas de extracción (heredadas del prompt de texto + específicas de imagen) |
| `VISION_SCHEMA` | `article_title` + `registros[]` con 9 campos, incluidos `source_ref` y `source_page` |

**Reglas del prompt de visión que no están en el de texto**:

- **Regla 2 — los gráficos no son fuente de datos.** Si la imagen es un bar,
  line o scatter plot, debe devolver `registros: []`. Leer un valor del eje es
  una estimación, y el pipeline prohíbe estimaciones.
- **Regla 4 — carry-down explícito.** Si el polímero está impreso una vez y
  aplica a varias filas (celda combinada, línea de cabecera), se arrastra a
  cada fila.
- **Regla 5 — códigos de muestra.** Si la tabla usa `PLA-2` / `PBAT50` y el
  texto define el mapeo, se resuelve y se registran **ambos**.

**Restricciones de `strict` que el código respeta**: todas las claves en
`required`, `additionalProperties: false`, sin campos opcionales, sin keywords
tipo `minLength` / `minimum` (varían por proveedor). Cubierto por
`test_gate_schema_is_strict_compatible`.

### 4.4 `src/vision_gate.py` — 341 líneas

**Qué decide**: si esta imagen merece que se le lean los dígitos.

| Función / clase | Responsabilidad |
|-----------------|-----------------|
| `ImageVerdict` | Modelo Pydantic + `field_validator` que rechaza `kind` desconocidos |
| `GateResult.should_extract` | **La política**: `contains_wvtr AND table_readable AND kind in EXTRACTABLE_KINDS` |
| `EXTRACTABLE_KINDS` | `{"data_table"}` — solo tablas |
| `prepare_image(cand, max_width)` | Reescala a 768 px con Pillow → control determinista del coste |
| `classify_image(client, cand, doi)` | Una llamada por imagen, con reintentos |
| `gate_all(client, candidates, doi, concurrency)` | Paralelo; devuelve `(resultados, fallos, stats)` |
| `_cache_key` / `_cache_load` / `_cache_store` | Caché por `sha1(modelo + prompt + bytes del PNG)` |
| `summarize_gates` / `gate_cost_estimate` | Recuento por `kind` y coste en USD |

**El campo `kind` validador doble**: el `json_schema` ya impone el enum, pero
si un endpoint degradara a texto libre, un `kind` inventado se colaría hasta
`EXTRACTABLE_KINDS`. El validador de Pydantic cierra esa puerta
(`test_verdict_rejects_unknown_kind`).

**La caché evita refacturar**: la clave incluye el hash de los bytes del PNG y
del prompt. Cambiar el prompt invalida la caché solo. Verificado: la segunda
corrida dio `gate_cached: 14, gate_calls: 0` y **$0 gastados**.

### 4.5 `src/llm_vision.py` — 210 líneas

**Qué decide**: cuáles son los valoresWVTR de una tabla rasterizada.

| Función / clase | Responsabilidad |
|-----------------|-----------------|
| `VISION_MODEL` | `openai/gpt-5.6-luna` — el mismo del texto |
| `VISION_MAX_IMAGES_PER_DOC` | 6 tope por documento |
| `ImageExtraction` | Resultado por imagen: registros, tokens, latencia, error |
| `pick_images(candidates, limit)` | Prioriza las de mayor `area_ratio` |
| `extract_from_image(...)` | Una llamada a Luna, `detail:"high"`, `seed=0` |
| `extract_all(...)` | Paralelo (concurrencia 4, más baja que el texto) |

**Por qué el mismo modelo**: `gpt-5.6-luna` ya declara
`input_modalities: [file, image, text]` y `structured_outputs` en el catálogo
de OpenRouter. No hay motivo para un segundo modelo de extracción.

**Por qué `seed=0` en lugar de `temperature=0`**: `temperature` **no** está
en los `supported_parameters` de gpt-5.6-luna; `seed` sí, y da la misma
reproducibilidad. El código de texto histórico sigue pasando `temperature=0`
en `main_test.py`, lo cual es un bug latente preexistente, no introducido aquí.

**Por qué concurrencia 4 y no 10**: son llamadas caras (`detail="high"` sobre
imágenes de hasta 2 MB), no texto. Subir la concurrencia arriesga rate limits
antes de ahorrar tiempo.

### 4.6 `src/reconcile.py` — 212 líneas

**Qué decide**: si un registro que viene del texto y otro que viene de la
imagen son **la misma medición** o dos distintas.

**Por qué**: una tabla rasterizada puede tener capa de texto, y
`opendataloader` a veces sí la recupera. Sin reconciliación, el mismo valor
entra dos veces y `replace_document` inserta filas duplicadas. Para un
meta-análisis eso vuelve la base de datos no fiable.

**Identidad de una medición**:

```
identidad = (polymer normalizado, valor normalizado, unidades normalizadas)
```

La temperatura, RH, espesor y método **no** forman parte de la identidad a
propósito: son metadatos que una de las dos rutas puede tener más completos, y
unificarlos es lo correcto.

| Función | Responsabilidad |
|---------|-----------------|
| `_norm_polymer` | Minúsculas, sin acentos, sin separadores: `PBAT-50` = `PBAT50` |
| `_norm_text` | Para unidades: `g/m²/day` = `G/M2/DAY` |
| `_to_float` | Entero de tablas: miles EN/FR/EU, decimal coma, notación científica, símbolos `~` `>` |
| `_norm_value` | 4 cifras significativas: `4.5` = `4,50` = `4.499999` |
| `identity(record)` | La tupla de arriba |
| `reconcile(text, image)` | Devuelve `(registros, ReconcileStats)` |
| `_richness(record)` | Cuántas condiciones tiene un registro |

**Reglas de fusión, en orden**:

1. Texto primero: es la fuente sin OCR de por medio.
2. Si la imagen coincide en identidad → **una sola fila**, se rellenan sus
   campos vacíos con los de la imagen, `source_kind='text+image'`,
   `corroborated=true`, y la referencia de la imagen se guarda aparte en
   `source_ref_image`.
3. Si solo hay imagen → entra con `source_kind='image'`.
4. Duplicados dentro de la misma ruta → se colapsan, conservando el más rico.

**`PBAT` y `PBAT50` NO se fusionan.** Son formulaciones distintas; unirlas
perdería un dato. Cubierto por
`test_different_sample_code_stays_a_separate_row`.

### 4.7 Tests — 566 líneas

| Archivo | Tests | Requiere red |
|---------|-------|--------------|
| `tests/test_vision_pipeline.py` | 54 | No |
| `tests/test_vision_e2e.py` | 3 | Sí (se salta sin `OPENROUTE_API`) |
| `tests/fixtures/make_wvtr_table.py` | generador | No |

El fixture genera una tabla WVTR rasterizada sintética con 5 filas y 7 columnas
de valores conocidos. Existe para probar el camino **positivo** con verdad
terreno exacta: cualquier valor inventado por el extractor se detecta.

```bash
.venv/bin/python -m unittest discover -s tests        # 54 unitarios
.venv/bin/python -m unittest tests.test_vision_e2e -v # 3 integración
```

---

## 5. Modelos y costes

### Dos modelos, deliberadamente

| Rol | Modelo | $/1M in/out | Por qué ese |
|-----|--------|-------------|-------------|
| Gate | `google/gemini-2.5-flash-lite` | 0.10 / 0.40 | Barato, 0 tokens de razonamiento, structured outputs |
| Extracción | `openai/gpt-5.6-luna` | 0.20 / 1.20 | Ya es el ganador del benchmark; visión + structured outputs |

### `gpt-5-nano` fue evaluado y descartado

Medido sobre la misma figura (1506×1025 px):

| Modelo | config | in | out | razonamiento | resultado | coste |
|--------|--------|----|-----|--------------|-----------|-------|
| `gpt-5-nano` | `effort=minimal`, 768px | 1279 | 72 | 0 | **clasificó mal** (`graphical_abstract` donde con razonamiento dijo `plot`) | $0.000093 |
| `gpt-5-nano` | default, maxtok 1500 | 1279 | 876 | 768 | `plot` correcto | $0.000414 |
| `gemini-2.5-flash-lite` | 768px | 743 | 78 | 0 | `plot` correcto | $0.000106 |
| `ling-3.0-flash-vl` | 768px | 897 | 300 | 208 | **no-JSON** (truncado) | $0.000108 |

El sesgo de nano con razonamiento mínimo va en la **dirección peligrosa**: una
tabla WVTR rasterizada mirada como "graphical abstract" produce
`contains_wvtr: false` → falso negativo irreversible. A precio casi idéntico,
Gemini gana.

`reasoning_effort='none'` fue rechazado explícitamente por el endpoint
(*"Reasoning is mandatory for this endpoint and cannot be disabled"*).

### `detail: "low"` es ignorado

OpenRouter y los proveedores ignoran el campo `detail`: con la imagen completa
el prompt costó **2971 tokens** en vez de **743**. Reescalar con Pillow a 768 px
es el único control determinista del coste, y es independiente del proveedor.

### Coste real medido

| Escenario | Coste |
|-----------|-------|
| Gate, 14 imágenes (paper real) | $0.003345 |
| Gate, 1 tabla WVTR | $0.000282 |
| Extracción Luna, 1 tabla → 5 registros | $0.000849 |
| **Total por paper con 1 tabla WVTR** | **~$0.004** |
| Gate sobre los 70 PDFs de R2 (≈980 imágenes) | **~$0.23** |

Coste de la ruta de texto actual: ~$0.003/paper. El incremento es de ~+65% si
cada paper tiene una tabla WVTR en imagen, y ~+5% si no tiene ninguna.

---

## 6. Esquema de base de datos

Cinco columnas de procedencia, añadidas de forma idempotente por
`ensure_schema()` (así una base existente migra sola en la siguiente corrida):

```sql
ALTER TABLE wvtr_data ADD COLUMN IF NOT EXISTS source_kind  TEXT;    -- text|image|text+image
ALTER TABLE wvtr_data ADD COLUMN IF NOT EXISTS source_ref   TEXT;    -- imageFile9.png
ALTER TABLE wvtr_data ADD COLUMN IF NOT EXISTS source_page  INTEGER; -- página 1-based
ALTER TABLE wvtr_data ADD COLUMN IF NOT EXISTS confidence   NUMERIC;
ALTER TABLE wvtr_data ADD COLUMN IF NOT EXISTS corroborated BOOLEAN DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_wvtr_source ON wvtr_data(source_kind);
```

`replace_document()` se extendió para escribirlas. Las funciones legacy
(`insert_wvtr`, `insert_wvtr_batch`) **no** se tocaron: listan sus columnas
explícitamente, así que siguen funcionando con `NULL` en las nuevas.

Consultas de ejemplo:

```sql
-- Filas que solo existen gracias a la ruta de imágenes
SELECT polymer, wvtr_value, source_ref, source_page
FROM wvtr_data WHERE source_kind = 'image';

-- Valores confirmados por dos lecturas independientes
SELECT doi, polymer, wvtr_value FROM wvtr_data WHERE corroborated = TRUE;
```

La idempotencia de `replace_document` (borrado por DOI antes de insertar) no
cambió, así que los registros fusionados no generan duplicados al re-correr.

---

## 7. Estado actual del repo

### Archivos nuevos (sin commitear)

```
src/image_manifest.py            265 líneas
src/image_filter.py              122 líneas
src/prompt_vision.py             229 líneas
src/vision_gate.py               341 líneas
src/llm_vision.py                210 líneas
src/reconcile.py                 212 líneas
tests/test_vision_pipeline.py    422 líneas
tests/test_vision_e2e.py         144 líneas
tests/fixtures/make_wvtr_table.py 68 líneas
tests/__init__.py
```

### Archivos modificados

```
 README.md            +119  arquitectura, esquema, costes, testing
 docker/init.sql       +23  columnas + índice + migración idempotente
 main_test.py         +239  process_images, EXTRACTOR_VERSION, métricas
 pyproject.toml         +2  pillow>=10.0.0, pypdf>=4.0.0
 src/database.py       +36  PROVENANCE_COLUMNS, ensure_schema, replace_document
 src/pdf_to_markdown.py +58 with_images, external, outline.json
                     -----
 total               +431  -46
```

`git status` muestra 7 archivos nuevos y 6 modificados. **Nada está
commiteado.**

### Estado de verificación

| Comprobación | Estado |
|--------------|--------|
| 54 tests unitarios | OK, sin red |
| 3 tests de integración contra la API real | OK |
| `ruff` en archivos nuevos | limpio |
| `main_test.py --dry-run` (70 PDFs) | arranca, plan correcto |
| Regresión texto (7 papers, 29 registros) | 0 campos alterados |
| `pdf_to_markdown(with_images=False)` | sin `![]`, sin `.json`, sin `images/` |
| BD real (`docker compose up` + fase 3) | **no ejecutada** |

### Lo que NO está verificado

1. **No se encontró una tabla WVTR rasterizada real en R2.** Se escanearon 25
   de los 70 PDFs buscando términos WVTR; solo uno los tenía
   (`10.1007_s11947-026-04605-6.pdf`), y es un *review* sin tabla de datos.
   El camino positivo se validó con el fixture sintético, **no** con un PDF
   real. Esto es la brecha más importante.
2. `benchmark/ground_truth.json` **no** se extendió con `expected_from_images`.
   Sin eso no hay forma objetiva de medir la precisión del gate a escala.
3. La Fase 3 nunca se ejecutó contra la BD real: las columnas de procedencia
   están escritas pero no verificadas en PostgreSQL.
4. El pipeline completo no se corrió sobre los 70 PDFs (serían ~980
   imágenes y ~$0.23 de gate).

### `images.txt`

Sin commitear, es el documento de plan previo. Proponía `image_output="separate"`
(inválido) y módulos con otros nombres. Se deja como referencia histórica;
**no debe usarse como guía**.

---

## 8. Cómo medimos si funciona bien

### 8.1 Métricas ya instrumentadas

Cada documento deja su rastro en
`temp/results/{stem}__gpt-5.6-luna.json`:

```json
{
  "_meta": {
    "extractor_version": "v2-vision",
    "images": {
      "images_total": 38,
      "images_heuristic_kept": 14,
      "heuristic_drops": {"tiny_px": 23, "few_colors": 1},
      "gate_kinds": {"plot": 9, "schematic": 2, "photo_micrograph": 3},
      "gate_wvtr_hits": 0,
      "gate_to_extract": 0,
      "gate_cost_usd": 0.003345,
      "gate_verdicts": [ /* un registro por imagen: kind, contains_wvtr,
                             table_readable, confidence, evidence */ ],
      "vision_records": 0,
      "vision_cost_usd": 0.0
    },
    "reconcile": {
      "text_records": 29, "image_records": 5, "duplicates_dropped": 0,
      "corroborated": 2, "fields_filled_from_image": 3
    }
  }
}
```

`gate_verdicts` incluye el campo `evidence` con la cita literal de lo que el
modelo vio. Eso permite **auditar falsos negativos sin volver a pagar**.

### 8.2 Los cuatro indicadores que hay que seguir

| Indicador | Dónde | Meta | Por qué |
|-----------|-------|------|---------|
| **Recall del gate** | manual, sobre `gate_verdicts` | ≥ 95 % de las tablas WVTR reales seleccionadas | El fallo caro. Un `contains_wvtr: false` falso es un dato perdido para siempre |
| **Precisión de la extracción** | `tests/fixtures` + ground truth | 100 % de filas, 0 valores inventados | Un valor inventado contamina el meta-análisis |
| **Tasa de `corroborated`** | `_meta.reconcile` | — | Señal de calidad: si texto e imagen coinciden, la lectura es fiable |
| **Ratio `image` / `total`** | SQL sobre `wvtr_data` | — | Porcentaje real de datos que la ruta de texto no veía |

```sql
SELECT source_kind, count(*) FROM wvtr_data GROUP BY 1;
SELECT count(*) FILTER (WHERE corroborated) * 100.0 / count(*)
  AS pct_corroborado FROM wvtr_data;
```

### 8.3 Protocolo de auditoría del gate

Para un lote de 20 papers:

1. Correr Fase 1 y Fase 2 sobre el lote.
2. Extraer de los JSON todos los `gate_verdicts` con
   `contains_wvtr: false`.
3. Abrir esas imágenes **manualmente** y contar cuántas eran realmente tablas
   WVTR. Ese es el recall.
4. Cualquier fallo del recall se corrige por el campo `evidence` (el modelo
   dijo qué vio) antes de tocar el prompt entero.

Este paso ya se hizo para 2 papers: recall 100 % en ambos, uno con 0 WVTR
(14 imágenes) y otro con 6 de 7 imágenes esquemáticas.

### 8.4 El número que decide si funciona

> **¿Cuántos registros WVTR nuevos aparecen con `source_kind = 'image'` que
> no estaban en el pipeline de texto?**

Ese es el retorno de la inversión. Si tras procesar el corpus completo
`source_kind = 'image'` es 0, el trabajo added no aportó nada y hay que revisar
el gate. La expectativa con los papers de SciDirect/Wiley de barred property
es de 10–25 % de las mediciones.

---

## 9. Qué falta hacer

### Prioridad alta

1. **Conseguir papers con tablas WVTR rasterizadas reales.** Ninguno de los 25
   escaneados tenía una. Hace falta un corpus con más papers de packaging, o
   construir un PDF de prueba a partir del fixture (insertar la imagen como
   tabla escaneada dentro de un PDF real) para validar el camino completo
   incluyendo `opendataloader`.
2. **Extender `benchmark/ground_truth.json`** con `expected_from_images` por
   paper, y escribir `benchmark/evaluate_vision.py` que reporte precisión y
   recall del gate contra esa verdad terreno.
3. **Verificar la BD**: `docker compose up -d`, correr Fase 3, comprobar que
   las 5 columnas se escriben y que `source_kind` se distribution bien.

### Prioridad media

4. **Correr el corpus completo** (70 PDFs) y revisar el ratio
   `image` / `total` y la distribución de `gate_kinds`.
5. **Limpiar `temp/markdown/images/`** al terminar cada lote. Actualmente las
   imágenes se conservan porque hacen falta para auditar; la decisión tomada
   fue "solo en temp/, desechables al final", pero el borrado **no** está
   implementado todavía.
6. **Arreglar el `temperature=0` preexistente** en `main_test.py:453`, que
   `gpt-5.6-luna` no soporta. Debería ser `seed=0`, como ya hace la ruta de
   visión.
7. **Quitar los 2 imports muertos** de `sync_state` (`completed_keys`,
   `failed_keys`) y `import psycopg2` en `database.py`.

### Prioridad baja

8. Considerar `image_resolution` > 144 DPI si las tablas densas salen
   ilegibles (`table_readable: false` con `contains_wvtr: true` será la señal
   de alarma en `gate_verdicts`).
9. Extraer la lógica de `prompt_vision.py` a un esquema compartido con
   `prompt.py` si se añaden más campos.

---

## 10. Resumen de decisiones y su justificación

| Decisión | Alternativa descartada | Motivo |
|----------|------------------------|--------|
| `image_output="external"` | `"on"` / `"separate"` | Los únicos valores válidos son `off\|embedded\|external` |
| `with_images=False` por defecto | Siempre activado | No rompe los otros 4 call sites |
| Manifiesto en Fase 1 | En Fase 2 | El PDF se borra; el tamaño de página solo se lee ahí |
| Gate con `json_schema` | Texto libre + regex | Los modelos devuelven `"Sí."`, párrafos, etc. |
| `gemini-2.5-flash-lite` para el gate | `gpt-5-nano` | Nano clasificaba mal con razonamiento mínimo; mismo precio |
| Reescalar a 768 px | `detail: "low"` | `detail` lo ignoran los proveedores (2971 → 743 tokens) |
| Filtro heurístico permisivo | Filtro agresivo | El gate es barato; un falso negativo es irreversible |
| Plots no se digitalizan | Leer el eje | Leer un eje es estimación; el pipeline prohíbe estimaciones |
| Solo `data_table` extraíble | `data_table` + `plot` | Coherente con la decisión anterior |
| Caché por sha1 | Caché por ruta | Si la imagen cambia, la clave cambia |
| `EXTRACTOR_VERSION` en `_meta` | Solo ETag | Sin esto, los papers ya extraídos sin imágenes nunca se reprocesarían |
| Identidad = polímero+valor+unidades | Identidad con condiciones | Condiciones son metadatos que se enriquecen, no parte de la identidad |
| `PBAT` ≠ `PBAT50` | Fusionar por polymer normalizado | Son formulaciones distintas |

---

## 11. Comandos de referencia

```bash
# Tests
.venv/bin/python -m unittest discover -s tests        # 54 unitarios, sin red
.venv/bin/python -m unittest tests.test_vision_e2e -v # 3 integración, con API
.venv/bin/python tests/fixtures/make_wvtr_table.py    # regenerar el fixture

# Lint
ruff check src/ tests/

# Pipeline
.venv/bin/python main_test.py --dry-run                # plan sin gastar tokens
.venv/bin/python main_test.py --phase 1                # descarga + manifiesto
.venv/bin/python main_test.py --phase 2                # texto + gate + visión
.venv/bin/python main_test.py --phase 3                # PostgreSQL

# Auditar el gate sin volver a pagar
cat temp/results/<stem>__gpt-5.6-luna.json | \
  .venv/bin/python -m json.tool | grep -A6 gate_verdicts
```