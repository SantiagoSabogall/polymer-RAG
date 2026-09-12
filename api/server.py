"""
server.py - API REST para RAG WVTR
===================================
FastAPI server para consultas RAG sobre datos WVTR.

Endpoints:
- GET /: Información de la API
- POST /query: Consulta RAG completa
- POST /search: Solo búsqueda de registros
- GET /health: Verificar estado
"""

import sys
import os
from pathlib import Path

# Agregar src/ al path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from rag import rag_query, search_similar
from database import get_connection, release_connection

# ============================================
# CONFIGURACIÓN DE LA API
# ============================================

app = FastAPI(
    title="WVTR RAG API",
    description="API para consultas RAG sobre datos WVTR de polímeros",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================
# MODELOS DE DATOS (Pydantic)
# ============================================

class QueryRequest(BaseModel):
    """
    Modelo para peticiones de consulta RAG.
    
    Ejemplo:
    {
        "question": "¿Cuál es el WVTR del PBAT?",
        "limit": 10
    }
    """
    question: str
    limit: int = 10


class QueryResponse(BaseModel):
    """
    Modelo para respuestas RAG.
    """
    answer: str
    sources: list
    context_used: str


# ============================================
# ENDPOINTS DE LA API
# ============================================

@app.get("/")
def root():
    """Endpoint raíz con información de la API."""
    return {
        "name": "WVTR RAG API",
        "version": "1.0.0",
        "description": "API para consultas RAG sobre datos WVTR de polímeros",
        "endpoints": {
            "/query": "POST - Consulta RAG completa",
            "/search": "POST - Solo búsqueda de registros",
            "/health": "GET - Verificar estado"
        }
    }


@app.post("/query", response_model=QueryResponse)
def query_rag(request: QueryRequest):
    """
    Consulta RAG completa.
    
    Retorna respuesta generada + fuentes utilizadas.
    """
    result = rag_query(request.question, request.limit)
    return QueryResponse(
        answer=result["answer"],
        sources=result["sources"],
        context_used=result["context_used"]
    )


@app.post("/search")
def search_records(request: QueryRequest):
    """
    Búsqueda de registros similares.
    
    Solo retorna los registros encontrados sin generar respuesta.
    """
    results = search_similar(request.question, request.limit)
    return {"results": results, "count": len(results)}


@app.get("/health")
def health_check():
    """Verificar estado de la API y base de datos."""
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM wvtr_data")
        count = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM wvtr_data WHERE embedding IS NOT NULL")
        with_embeddings = cur.fetchone()[0]
        cur.close()
        
        return {
            "status": "healthy",
            "database": "connected",
            "records": count,
            "records_with_embeddings": with_embeddings
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }
    finally:
        if conn:
            release_connection(conn)


# ============================================
# EJECUTAR EL SERVIDOR
# ============================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
