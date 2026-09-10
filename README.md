# WVTR Extraction Pipeline

## Automated Water Vapor Transmission Rate Data Extraction from Polymer Science Literature

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-12+-336791.svg)](https://www.postgresql.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

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

---

## 2. Installation

### 2.1 Prerequisites

- Python 3.10 or higher
- PostgreSQL 12 or higher
- Cloudflare R2 account (for PDF storage)
- OpenRouter API key (for LLM access)

### 2.2 Clone Repository

```bash
git clone https://github.com/your-username/wvtr-extraction.git
cd wvtr-extraction
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
pip install -r requirements.txt
```

### 2.5 Configure Environment

Create `API_KEY.env` in the project root:

```env
# OpenRouter API Key
OPENROUTE_API=sk-or-v1-your-api-key-here

# Cloudflare R2 Credentials
R2_ACCESS_KEY=your-r2-access-key
R2_SECRET_KEY=your-r2-secret-key
R2_ENDPOINT=https://your-account.r2.cloudflarestorage.com
R2_BUCKET_NAME=your-bucket-name

# PostgreSQL Credentials
PG_HOST=localhost
PG_DATABASE=polymers_wvtr
PG_USER=postgres
PG_PASSWORD=your-password
```

### 2.6 Initialize Database

```sql
-- Connect to PostgreSQL and create database
CREATE DATABASE polymers_wvtr;

-- Connect to the database and create table
\c polymers_wvtr

CREATE TABLE wvtr_data (
    id SERIAL PRIMARY KEY,
    article_title TEXT,
    doi TEXT,
    polymer TEXT,
    wvtr_value FLOAT,
    wvtr_units TEXT,
    temperature TEXT,
    rh TEXT,
    thickness TEXT,
    test_method TEXT,
    pdf_url TEXT,
    raw_json JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Create index for DOI lookups
CREATE INDEX idx_wvtr_doi ON wvtr_data(doi);
CREATE INDEX idx_wvtr_polymer ON wvtr_data(polymer);
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

### 3.2 Benchmark Models

Compare extraction performance across multiple LLMs:

```bash
# DeepSeek V4 Flash
python benchmark/test_7.py

# GPT-5.6 Luna
python benchmark/test_gpt56.py

# Gemini 3.8 Flash
python benchmark/test_gemini38.py

# GLM-5.3 Flash
python benchmark/test_glm53.py

# Generate comparison Excel
python benchmark/generate_excel.py
```

### 3.3 Database Exploration

```bash
# Python exploration script
python benchmark/explore_db.py
python benchmark/explore_db.py --export
python benchmark/explore_db.py --search PBAT

# SQL exploration
psql -U postgres -d polymers_wvtr -f benchmark/explore.sql
```

### 3.4 DBeaver Connection

1. Open DBeaver → New Database Connection → PostgreSQL
2. Configure:
   - Host: `localhost`
   - Port: `5432`
   - Database: `polymers_wvtr`
   - User: `postgres`
   - Password: `your-password`
3. Click "Test Connection" → "Finish"

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
wvtr-extraction/
├── src/                          # Source modules
│   ├── config.py                 # Configuration management
│   ├── cloudfareR2.py            # Cloudflare R2 integration
│   ├── pdf_to_markdown.py        # PDF → Markdown conversion
│   ├── clean_markdown.py         # Markdown cleaning (regex)
│   ├── prompt.py                 # LLM prompts
│   ├── llm.py                    # LLM interaction utilities
│   ├── database.py               # PostgreSQL with pooling
│   └── monitoring.py             # Structured logging & metrics
├── benchmark/                    # Benchmarking tools
│   ├── test_7.py                 # DeepSeek benchmark
│   ├── test_gpt56.py             # GPT-5.6 benchmark
│   ├── test_gemini38.py          # Gemini benchmark
│   ├── test_glm53.py             # GLM benchmark
│   ├── run_benchmark.py          # Multi-model benchmark
│   ├── evaluate.py               # Ground truth evaluation
│   ├── generate_excel.py         # Excel report generator
│   ├── explore_db.py             # Database exploration
│   └── explore.sql               # SQL queries
├── main_test.py                  # Main pipeline entry point
├── main.py                       # Alternative pipeline
├── API_KEY.env                   # Environment variables (gitignored)
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

```bash
pip freeze > requirements.txt
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

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 11. Citation

If you use this software in your research, please cite:

```bibtex
@software{wvtr_pipeline2026,
  author = {Sabogal, Sebastian},
  title = {WVTR Extraction Pipeline: Automated Water Vapor Transmission Rate Data Extraction},
  year = {2026},
  url = {https://github.com/your-username/wvtr-extraction}
}
```

---

## 12. Contact

For questions or collaborations:

- **Email**: your-email@institution.edu
- **GitHub**: https://github.com/your-username

---

*Last updated: September 2026*
