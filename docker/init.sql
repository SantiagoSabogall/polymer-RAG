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
    embedding vector(1536),
    -- Procedencia del dato (ruta de texto y/o de imagen).
    -- source_kind: 'text' | 'image' | 'text+image'
    -- source_ref : nombre del PNG de origen, p.ej. 'imageFile9.png'
    source_kind TEXT,
    source_ref TEXT,
    source_page INTEGER,
    confidence NUMERIC,
    corroborated BOOLEAN DEFAULT FALSE
);

-- Crear índices
-- Nota: sin UNIQUE(doi): un artículo tiene N filas (una por registro WVTR).
-- La idempotencia la garantiza replace_document() (borrado previo por DOI/pdf_url).
CREATE INDEX IF NOT EXISTS idx_wvtr_doi ON wvtr_data(doi);
CREATE INDEX IF NOT EXISTS idx_wvtr_polymer ON wvtr_data(polymer);
CREATE INDEX IF NOT EXISTS idx_wvtr_pdf_url ON wvtr_data(pdf_url);
CREATE INDEX IF NOT EXISTS idx_wvtr_source ON wvtr_data(source_kind);
CREATE INDEX IF NOT EXISTS idx_wvtr_embedding ON wvtr_data 
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- Migración para bases creadas antes de la ruta de imágenes.
-- ensure_schema() en database.py hace lo mismo en caliente; esto cubre el
-- caso de levantar el contenedor con una base ya existente.
ALTER TABLE wvtr_data ADD COLUMN IF NOT EXISTS source_kind TEXT;
ALTER TABLE wvtr_data ADD COLUMN IF NOT EXISTS source_ref TEXT;
ALTER TABLE wvtr_data ADD COLUMN IF NOT EXISTS source_page INTEGER;
ALTER TABLE wvtr_data ADD COLUMN IF NOT EXISTS confidence NUMERIC;
ALTER TABLE wvtr_data ADD COLUMN IF NOT EXISTS corroborated BOOLEAN DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_wvtr_source ON wvtr_data(source_kind);

-- Verificar instalación
SELECT 'pgvector installed successfully' as status;
