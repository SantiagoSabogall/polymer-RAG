-- ============================================
-- INICIALIZACIÓN DE BASE DE DATOS
-- ============================================

-- Habilitar pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Crear tabla wvtr_data
CREATE TABLE IF NOT EXISTS wvtr_data (
    id SERIAL PRIMARY KEY,
    article_title TEXT,
    doi TEXT,
    polymer TEXT NOT NULL,
    wvtr_value NUMERIC,
    wvtr_units TEXT,
    temperature TEXT,
    rh TEXT,
    thickness TEXT,
    test_method TEXT,
    pdf_url TEXT,
    raw_json JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    embedding vector(1536)
);

-- Crear índices
-- Nota: sin UNIQUE(doi): un artículo tiene N filas (una por registro WVTR).
-- La idempotencia la garantiza replace_document() (borrado previo por DOI/pdf_url).
CREATE INDEX IF NOT EXISTS idx_wvtr_doi ON wvtr_data(doi);
CREATE INDEX IF NOT EXISTS idx_wvtr_polymer ON wvtr_data(polymer);
CREATE INDEX IF NOT EXISTS idx_wvtr_pdf_url ON wvtr_data(pdf_url);
CREATE INDEX IF NOT EXISTS idx_wvtr_embedding ON wvtr_data 
USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- Verificar instalación
SELECT 'pgvector installed successfully' as status;
