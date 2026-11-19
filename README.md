# WVTR Extraction Pipeline

## Automated Water Vapor Transmission Rate Data Extraction from Polymer Science Literature

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-18+-336791.svg)](https://www.postgresql.org/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)

---

## Abstract

This repository implements an automated pipeline for extracting Water Vapor Transmission Rate (WVTR) measurements from polymer science literature. The system processes scientific PDFs, converts them to structured markdown, and utilizes Large Language Models (LLMs) to extract experimental WVTR data with associated conditions (polymer type, temperature, relative humidity, film thickness, and test method). Extracted data is stored in a PostgreSQL database for systematic analysis and retrieval.

Since v2, the pipeline also reads **images**: tables that arrive as a rasterized picture instead of extractable text are recovered by a vision model.

---

## 1. Scientific Context

### 1.1 Problem Statement

Water Vapor Transmission Rate (WVTR) is a critical metric for evaluating the barrier properties of polymer films used in food packaging applications. However, WVTR data is scattered across thousands of published research papers, making systematic meta-analysis challenging. Manual extraction is time-consuming, error-prone, and not reproducible.

### 1.2 Solution

This pipeline automates the extraction process:

```
PDF Literature → Markdown + Images → LLM Extraction → Structured JSON → PostgreSQL Database
```

### 1.3 Key Features

- **Automated PDF Processing**: Downloads PDFs from Cloudflare R2, converts to markdown, and cleans formatting artifacts
- **Text Extraction**: GPT-5.6 Luna (via OpenRouter) for structured data extraction
- **Image Extraction**: recovers WVTR tables that are rasterized pictures, via a cheap vision gate followed by the same GPT-5.6 Luna
- **Deduplication**: Prevents duplicate entries by tracking DOI identifiers; reconciles values seen in both text and image into a single row
- **Provenance**: every row records whether it came from `text`, `image`, or `text+image`
- **Connection Pooling**: Efficient PostgreSQL connection management for batch operations
- **Structured Logging**: JSON-formatted logs for reproducibility and audit trails
- **Cost Tracking**: Monitors API usage and associated costs

### 1.4 Typography

The application uses the following typography system:

| Font | Usage | Source |
|------|-------|--------|
| **Inter** | Body text, UI elements, chat messages | Google Fonts |
| **Lora** | Headings and titles | Google Fonts |

```css
/* Inter for body text */
font-family: 'Inter', sans-serif;

/* Lora for headings */
h1, h2, h3 { font-family: 'Lora', serif; }
```

---

## 2. Installation

### 2.1 Prerequisites

- Python 3.10 or higher
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (package manager)
- Docker + Docker Compose
- Cloudflare R2 account (for PDF storage)
- OpenRouter API key (for LLM access)

### 2.2 Clone Repository

```bash
git clone https://github.com/SantiagoSabogall/polymer-RAG.git
cd polymer-RAG
```

### 2.3 Install Dependencies

```bash
uv sync
```

This creates a virtual environment and installs all dependencies. Optional extras:

```bash
uv sync --extra dev       # + testing/linting tools
uv sync --extra benchmark # + Excel export
uv sync --all-extras      # + everything
```

### 2.4 Available Commands

After installation, you can run:

| Command | Description |
|---------|-------------|
| `uv run pipeline` | Run the complete extraction pipeline |
| `uv run api-server` | Start the REST API server |
| `uv run embeddings` | Generate vector embeddings |
| `uv run benchmark-run <model>` | Run benchmark with specific model |
| `uv run streamlit run app/chat.py` | Start Streamlit chat interface |

### 2.5 Configure Environment

The project uses **two configuration files**:

#### `.env` — Docker Compose (PostgreSQL)

```bash
cp .env.example .env
nano .env
```

```env
PG_DATABASE=polymers_wvtr
PG_USER=postgres
PG_PASSWORD=your_password_here
```

#### `API_KEY.env` — Python (API keys + credentials)

```bash
cp API_KEY.env.example API_KEY.env
nano API_KEY.env
```

```env
# OpenRouter (LLM access)
OPENROUTE_API=sk-or-v1-your-key-here

# Cloudflare R2 (PDF storage)
R2_ACCESS_KEY=your_r2_access_key
R2_SECRET_KEY=your_r2_secret_key
R2_ENDPOINT=https://your-account-id.r2.cloudflarestorage.com
R2_BUCKET_NAME=your-bucket-name

# PostgreSQL (same password as .env)
PG_HOST=localhost
PG_PORT=5433
PG_DATABASE=polymers_wvtr
PG_USER=postgres
PG_PASSWORD=your_password_here
```

### 2.6 Start Infrastructure

```bash
docker compose up -d
```

This starts:
- **PostgreSQL** (port 5433) with pgvector extension and auto-initialized schema
- **Adminer** (port 8080) for web-based database exploration

Verify both services are running:

```bash
docker ps
# Should show: wvtr-postgres and wvtr-adminer
```

---

## 3. Usage

### 3.1 Complete Pipeline

Process all PDFs from Cloudflare R2 through the entire pipeline:

```bash
uv run pipeline
# or
uv run python main.py
```

This executes:
1. **Phase 1**: Download PDFs → Convert to Markdown → Clean
2. **Phase 2**: Extract WVTR data using GPT-5.6 Luna
3. **Phase 3**: Insert results into PostgreSQL

### 3.2 Generate Embeddings

After the pipeline completes, generate vector embeddings for RAG search:

```bash
uv run embeddings
# or
uv run python scripts/generate_embeddings.py
```

### 3.3 Explore Data

#### Adminer (Web Interface)

Open [http://localhost:8080](http://localhost:8080) and login:

| Field | Value |
|-------|-------|
| System | PostgreSQL |
| Server | postgres |
| Username | postgres |
| Password | (your PG_PASSWORD) |
| Database | polymers_wvtr |

From there you can:
- Browse tables and data
- Run SQL queries
- Export data to CSV/JSON
- Edit records directly

#### Python Script

```bash
python benchmark/explore_db.py
python benchmark/explore_db.py --export
python benchmark/explore_db.py --search PBAT
```

### 3.4 Use the System

#### Chat Interface (Streamlit)

```bash
uv run streamlit run app/chat.py
# Open http://localhost:8501
```

#### API REST

```bash
uv run api-server &
# Open http://localhost:8001/docs for Swagger UI
```

```bash
# Query example
curl -X POST http://localhost:8001/query \
  -H "Content-Type: application/json" \
  -d '{"question": "¿Cuál es el WVTR del PBAT?", "limit": 5}'
```

### 3.5 Benchmark Models

Run a model on the Markdown corpus. Each model writes an aggregate
`results-<model hash>.json` under `temp/benchmark/`, with per-document caches
keyed by input content, model and prompt. Failed responses are retried on the next run.

```bash
uv run benchmark-run openai/gpt-5.6-luna --input-dir benchmark/papers --output-dir temp/benchmark
uv run benchmark-evaluate --results temp/benchmark/results-MODEL_HASH.json --ground-truth benchmark/ground_truth.json
uv run python benchmark/generate_excel.py
```

Use the actual results path printed by the runner. Evaluation excludes papers without
an explicit `expected` annotation and reports their names and coverage. An annotated
empty list means the paper has no WVTR measurements. Matching requires polymer,
verbatim units and a numeric value within 10% relative tolerance; field accuracy is
reported separately. The supplied ground-truth file is an example and still needs
annotations for the seven corpus papers. Zero evaluated papers returns exit status 1.

### Pipeline entry points and installation

`pipeline`, `python main.py`, `python main_test.py` and `python -m src.pipeline`
invoke the same text-and-image pipeline. The existing phase flags remain available.
`main.py` now includes the image route. Current progress stays in
`temp/progress_test.json`, `temp/dois_test.json` and `temp/results/`.
Older `main.py` files such as `progress.json` are retained; their text-only results
are not automatically certified as current image extractions.

```bash
pipeline --phase 1 --dry-run
pipeline --phase 1
pipeline --phase 2
pipeline --phase 3 --no-prune
```

By default, data is stored under `./temp` and credentials are read from
`./API_KEY.env`, relative to the working directory. Set `WVTR_DATA_DIR`
and `WVTR_ENV_FILE` to absolute paths when running from another directory.
Environment variables take precedence over the credential file.

```bash
uv build
uv pip install dist/wvtr_extraction-1.3.0-py3-none-any.whl
pipeline --help
benchmark-run --help
benchmark-evaluate --help
```

### 3.6 Services Summary

| Port | Service | URL |
|------|---------|-----|
| 5433 | PostgreSQL | `localhost:5433` |
| 8080 | Adminer | [http://localhost:8080](http://localhost:8080) |
| 8001 | API REST | [http://localhost:8001](http://localhost:8001) |
| 8001/docs | Swagger | [http://localhost:8001/docs](http://localhost:8001/docs) |
| 8501 | Streamlit | [http://localhost:8501](http://localhost:8501) |

---

## 4. Architecture

### 4.1 System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    WVTR Extraction Pipeline                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐            │
│  │  Cloudflare  │    │   Worker    │    │    LLM      │            │
│  │     R2       │───→│  (Python)   │───→│  (GPT 5.6)  │            │
│  └─────────────┘    └─────────────┘    └─────────────┘            │
│                           │                   │                     │
│                           ▼                   ▼                     │
│                    ┌─────────────┐    ┌─────────────┐            │
│                    │  Progress   │    │   Cost      │            │
│                    │  Tracking   │    │  Tracking   │            │
│                    └─────────────┘    └─────────────┘            │
│                           │                   │                     │
│                           └───────────────────┘                     │
│                                           │                         │
│                                           ▼                         │
│                                    ┌─────────────┐                 │
│                                    │ PostgreSQL  │                 │
│                                    │   (Pool)    │                 │
│                                    └─────────────┘                 │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 Image Pipeline (v2)

WVTR tables are frequently published as pictures rather than text. `opendataloader-pdf` already converts *vector* tables into HTML, but a rasterized table produces no text at all — the data was invisible to the pipeline.

Four stages, each answering a different question:

| Stage | Question | Cost | Module |
|-------|----------|------|--------|
| Manifest | What images exist, and where? | free (from `outline.json`) | `src/image_manifest.py` |
| Heuristic | Is it obviously not a data table? | free (Pillow) | `src/image_filter.py` |
| Gate | Does it actually *print* WVTR values? | ~$0.0003/image | `src/vision_gate.py` |
| Extract | What are the values? | ~$0.0009/image | `src/llm_vision.py` |

Measured on a real Springer paper: **38 images extracted → 24 discarded for free → 14 gated → 0 selected** (the paper contained no WVTR data). On a synthetic rasterized WVTR table: 14 gated → 1 selected → **5/5 rows extracted with every field correct**.

**Two models, deliberately:**

| Role | Model | $/1M in/out | Why |
|------|-------|-------------|-----|
| Gate | `google/gemini-2.5-flash-lite` | 0.10 / 0.40 | Cheap, zero reasoning tokens, structured outputs |
| Extract | `openai/gpt-5.6-luna` | 0.20 / 1.20 | Already the benchmark winner; vision + structured outputs |

`gpt-5-nano` is $0.05/M in but was rejected: with `reasoning_effort=minimal` it mislabeled a rheology plot as a `graphical_abstract`, and that bias runs in the dangerous direction — a rasterized WVTR table dismissed as a graphical abstract is an irreversible false negative.

**Plots are not digitized.** Bar/line charts encode values as geometry, and reading a value off an axis is an estimate, not a measurement. The gate marks them `table_readable: false` and they are dropped. They can still corroborate values found elsewhere.

**Why a gate at all?** At ~$0.0003 per image, sending all extracted images to the extractor would cost ~$0.0025 each. On a 38-image paper that is a 80x increase for zero precision gain, since most images are publisher logos and chemical schemes.

### 4.3 Directory Structure

```
polymer-RAG/
├── src/                          # Source modules
│   ├── config.py                 # Configuration management
│   ├── cloudfareR2.py            # Cloudflare R2 integration
│   ├── pdf_to_markdown.py        # PDF → Markdown (+ images + outline.json)
│   ├── clean_markdown.py         # Markdown cleaning (regex)
│   ├── prompt.py                 # Text LLM prompt
│   ├── prompt_vision.py          # Gate + vision prompts and JSON schemas
│   ├── llm.py                    # Text LLM interaction utilities
│   ├── image_manifest.py         # Image inventory (page, bbox, PNG stats)
│   ├── image_filter.py           # Free heuristic discards
│   ├── vision_gate.py            # Typesafe "does this print WVTR?"
│   ├── llm_vision.py             # WVTR extraction from images
│   ├── reconcile.py              # Text + image merge and dedup
│   ├── database.py               # PostgreSQL with pooling
│   ├── embeddings.py             # Embedding generation
│   ├── rag.py                    # RAG engine (search + generation)
│   └── monitoring.py             # Structured logging & metrics
├── tests/
│   ├── test_vision_pipeline.py   # 54 unit tests, no network
│   ├── test_vision_e2e.py        # 3 integration tests (need OPENROUTE_API)
│   └── fixtures/                 # Synthetic rasterized WVTR table
├── api/                          # REST API
│   └── server.py                 # FastAPI server
├── app/                          # Web UI
│   └── chat.py                   # Streamlit chat interface
├── scripts/                      # Utility scripts
│   └── generate_embeddings.py    # Batch embedding generation
├── benchmark/                    # Benchmarking tools
│   ├── run_model.py              # Unified benchmark runner
│   ├── evaluate.py               # Ground truth evaluation
│   ├── generate_excel.py         # Excel report generator
│   ├── explore_db.py             # Database exploration
│   └── papers/                   # Benchmark PDF papers
├── docker/                       # Docker configuration
│   ├── init.sql                  # Database schema initialization
│   └── Dockerfile.postgres       # Custom PostgreSQL image
├── main_test.py                  # Compatibility entry point for src.pipeline
├── main.py                       # Compatibility entry point for src.pipeline
├── docker-compose.yml            # Docker services
├── .env                          # Docker variables (gitignored)
├── API_KEY.env                   # Python credentials (gitignored)
├── .env.example                  # Docker template
├── API_KEY.env.example           # Python template
└── README.md                     # This file
```

---

## 5. Configuration

### 5.1 Pipeline Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MODEL` | `openai/gpt-5.6-luna` | LLM model for extraction |
| `MAX_CONCURRENT_LLM` | `10` | Concurrent LLM requests |
| `MAX_WORKERS_DOWNLOAD` | `5` | Parallel download workers |
| `MAX_RETRIES_LLM` | `3` | Retry attempts per file |
| `BATCH_SIZE` | `50` | PDFs per download batch |

### 5.2 LLM Pricing (per 1M tokens)

| Model | Input | Output |
|-------|-------|--------|
| GPT-5.6 Luna | $0.20 | $1.20 |
| DeepSeek V4 Flash | $0.05 | $0.10 |
| Gemini 3.8 Flash | $0.75 | $3.75 |
| GLM-5.3 Flash | $0.075 | $0.25 |

---

## 6. Database Schema

### 6.1 Table: `wvtr_data`

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL | Primary key |
| `article_title` | TEXT | Title of the research article |
| `doi` | TEXT | Digital Object Identifier |
| `polymer` | TEXT | Polymer name/type |
| `wvtr_value` | FLOAT | WVTR measurement value |
| `wvtr_units` | TEXT | Units (e.g., g/m²/day) |
| `temperature` | TEXT | Test temperature |
| `rh` | TEXT | Relative humidity |
| `thickness` | TEXT | Film thickness |
| `test_method` | TEXT | ASTM/ISO standard |
| `pdf_url` | TEXT | Source PDF reference |
| `raw_json` | JSONB | Complete extraction result |
| `source_kind` | TEXT | `text` \| `image` \| `text+image` |
| `source_ref` | TEXT | Image file, e.g. `imageFile9.png` (image rows) |
| `source_page` | INTEGER | 1-based page of the image |
| `confidence` | NUMERIC | Extractor confidence, when available |
| `corroborated` | BOOLEAN | Value seen in **both** text and image |
| `created_at` | TIMESTAMP | Record creation time |
| `embedding` | vector(1536) | pgvector embedding |

The provenance columns are added idempotently by `ensure_schema()`, so existing
databases migrate on the next run without manual SQL.

### 6.2 Example Query

```sql
-- Rows that only exist because of the image pipeline
SELECT article_title, polymer, wvtr_value, wvtr_units, source_ref, source_page
FROM wvtr_data
WHERE source_kind = 'image'
ORDER BY wvtr_value;

-- Values confirmed by two independent readings (text and figure)
SELECT doi, polymer, wvtr_value, source_kind
FROM wvtr_data
WHERE corroborated = TRUE;
```

---

## 7. Benchmark Results

### 7.1 Model Comparison (7 PDFs)

| Model | Time | Records | Accuracy | Cost |
|-------|------|---------|----------|------|
| GPT-5.6 Luna | 14.8s | 29 | High | $0.022 |
| GLM-5.3 Flash | 143.7s | 26 | Medium | $0.013 |
| DeepSeek V4 Flash | 492s | 23 | Medium | $0.006 |
| Gemini 3.8 Flash | 73.7s | 17 | Low | $0.068 |

### 7.2 Recommendation

**GPT-5.6 Luna** is recommended for production use due to:
- Fastest processing time (9.1s average per PDF)
- Highest extraction accuracy
- Proper handling of scientific notation
- Reasonable cost ($0.003 per PDF)

---

## 8. Reproducibility

### 8.1 Environment

Dependencies are managed via `pyproject.toml`. To install:

```bash
uv sync
```

To update dependencies after modifying `pyproject.toml`:

```bash
uv sync
```

### 8.2 Data Versioning

```bash
# Tag stable versions
git tag -a v1.1-core -m "Core improvements + Monitoring"

# View tags
git tag -l

# Checkout specific version
git checkout v1.1-core
```

### 8.3 Testing

```bash
# 54 unit tests: manifest geometry, heuristics, schemas, value parsing, dedup.
# No network, no PDF, no API key.
.venv/bin/python -m unittest discover -s tests

# 3 integration tests: real gate + real extractor over a synthetic rasterized
# WVTR table with known ground truth. Skipped automatically without a key.
.venv/bin/python -m unittest tests.test_vision_e2e -v

# Regenerate the ground-truth fixture
.venv/bin/python tests/fixtures/make_wvtr_table.py

# Lint
ruff check src/ tests/
```

### 8.4 Logging

All pipeline runs generate structured logs in `temp/pipeline.log`:

```json
{
  "timestamp": "2026-09-09T03:01:29.790898",
  "event_type": "pipeline_end",
  "data": {
    "total_pdfs": 8,
    "processed": 0,
    "llm_records": 31,
    "duration_seconds": 0.775
  }
}
```

Phase 2 also logs a per-document image summary:

```
[IMAGES] paper.md: 38 extraidas, 14 tras heuristica (drops={'tiny_px': 23, 'few_colors': 1})
[GATE] imageFile27.png (p.5, 1506px->768px) kind=plot wvtr=False legible=False conf=1.00 -> descartar
[VISION] imageFile3.png (p.9): 12 registros, 1442/467 tokens, 6.1s
[RECONCILE] paper.md: texto=29 imagen=5 -> final=32 (corroborados=2, colapsados=0)
```

A per-image `gate_verdicts` array is also written to
`temp/results/{stem}__gpt-5.6-luna.json`, so gate false negatives can be audited
without re-running or re-paying.

---

## 9. Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/improvement`)
3. Commit changes (`git commit -m 'Add improvement'`)
4. Push to branch (`git push origin feature/improvement`)
5. Open a Pull Request

---

## 10. License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

---

## 11. Citation

If you use this software in your research, please cite:

```bibtex
@software{wvtr_pipeline2026,
  author = {Sabogal, Santiago},
  title = {WVTR Extraction Pipeline: Automated Water Vapor Transmission Rate Data Extraction},
  year = {2026},
  url = {https://github.com/SantiagoSabogall/polymer-RAG}
}
```

---

## 12. Contact

For questions or collaborations:

- **GitHub**: https://github.com/SantiagoSabogall

---

*Last updated: September 2026*
