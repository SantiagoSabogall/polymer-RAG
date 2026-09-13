# WVTR Extraction Pipeline

## Automated Water Vapor Transmission Rate Data Extraction from Polymer Science Literature

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-18+-336791.svg)](https://www.postgresql.org/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)

---

## Abstract

This repository implements an automated pipeline for extracting Water Vapor Transmission Rate (WVTR) measurements from polymer science literature. The system processes scientific PDFs, converts them to structured markdown, and utilizes Large Language Models (LLMs) to extract experimental WVTR data with associated conditions (polymer type, temperature, relative humidity, film thickness, and test method). Extracted data is stored in a PostgreSQL database for systematic analysis and retrieval.

---

## 1. Scientific Context

### 1.1 Problem Statement

Water Vapor Transmission Rate (WVTR) is a critical metric for evaluating the barrier properties of polymer films used in food packaging applications. However, WVTR data is scattered across thousands of published research papers, making systematic meta-analysis challenging. Manual extraction is time-consuming, error-prone, and not reproducible.

### 1.2 Solution

This pipeline automates the extraction process:

```
PDF Literature → Markdown → LLM Extraction → Structured JSON → PostgreSQL Database
```

### 1.3 Key Features

- **Automated PDF Processing**: Downloads PDFs from Cloudflare R2, converts to markdown, and cleans formatting artifacts
- **LLM-Based Extraction**: Utilizes GPT-5.6 Luna (via OpenRouter) for structured data extraction
- **Deduplication**: Prevents duplicate entries by tracking DOI identifiers
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
- Docker + Docker Compose
- Cloudflare R2 account (for PDF storage)
- OpenRouter API key (for LLM access)

### 2.2 Clone Repository

```bash
git clone https://github.com/SantiagoSabogall/polymer-RAG.git
cd polymer-RAG
```

### 2.3 Create Virtual Environment

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
venv\Scripts\activate  # Windows
```

### 2.4 Install Dependencies

```bash
pip install -e .
```

This installs all dependencies and the project itself. Optional extras:

```bash
pip install -e ".[dev]"      # + testing/linting tools
pip install -e ".[benchmark]" # + Excel export
pip install -e ".[all]"       # + everything
```

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
python main_test.py
```

This executes:
1. **Phase 1**: Download PDFs → Convert to Markdown → Clean
2. **Phase 2**: Extract WVTR data using GPT-5.6 Luna
3. **Phase 3**: Insert results into PostgreSQL

### 3.2 Generate Embeddings

After the pipeline completes, generate vector embeddings for RAG search:

```bash
python scripts/generate_embeddings.py
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
streamlit run app/chat.py
# Open http://localhost:8501
```

#### API REST

```bash
python api/server.py &
# Open http://localhost:8001/docs for Swagger UI
```

```bash
# Query example
curl -X POST http://localhost:8001/query \
  -H "Content-Type: application/json" \
  -d '{"question": "¿Cuál es el WVTR del PBAT?", "limit": 5}'
```

### 3.5 Benchmark Models

Compare extraction performance across multiple LLMs:

```bash
python benchmark/run_model.py openai/gpt-5.6-luna
python benchmark/run_model.py deepseek/deepseek-v4-flash-0731
python benchmark/run_model.py google/gemini-3.8-flash
python benchmark/run_model.py z-ai/glm-5.3-flash

# Generate comparison Excel
python benchmark/generate_excel.py
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

### 4.2 Directory Structure

```
polymer-RAG/
├── src/                          # Source modules
│   ├── config.py                 # Configuration management
│   ├── cloudfareR2.py            # Cloudflare R2 integration
│   ├── pdf_to_markdown.py        # PDF → Markdown conversion
│   ├── clean_markdown.py         # Markdown cleaning (regex)
│   ├── prompt.py                 # LLM prompts
│   ├── llm.py                    # LLM interaction utilities
│   ├── database.py               # PostgreSQL with pooling
│   ├── embeddings.py             # Embedding generation
│   ├── rag.py                    # RAG engine (search + generation)
│   └── monitoring.py             # Structured logging & metrics
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
├── main_test.py                  # Main pipeline entry point
├── main.py                       # Alternative pipeline
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
| `created_at` | TIMESTAMP | Record creation time |

### 6.2 Example Query

```sql
-- Find all PBAT measurements
SELECT 
    article_title,
    polymer,
    wvtr_value,
    wvtr_units,
    temperature,
    rh
FROM wvtr_data 
WHERE polymer ILIKE '%PBAT%'
ORDER BY wvtr_value;
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
pip install -e .
```

To update dependencies after modifying `pyproject.toml`:

```bash
pip install -e .
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

### 8.3 Logging

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
  author = {Sabogal, Sebastian},
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
