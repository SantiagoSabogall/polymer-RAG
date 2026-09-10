# Guía de Aprendizaje: RAG para WVTR Data

## Índice

1. [Conceptos Fundamentales](#1-conceptos-fundamentales)
2. [Configurar el Entorno](#2-configurar-el-entorno)
3. [Crear el Módulo de Embeddings](#3-crear-el-módulo-de-embeddings)
4. [Crear el Módulo RAG](#4-crear-el-módulo-rag)
5. [Crear la API](#5-crear-la-api)
6. [Probar el Sistema](#6-probar-el-sistema)
7. [Ejemplos de Uso](#7-ejemplos-de-uso)
8. [Costos](#8-costos)
9. [Solución de Problemas](#9-solución-de-problemas)
10. [Siguientes Pasos](#10-siguientes-pasos)

---

## 1. Conceptos Fundamentales

### 1.1 ¿Qué es RAG?

**RAG (Retrieval-Augmented Generation)** es una técnica que combina:

1. **Retrieval (Búsqueda):** Buscar información relevante en una base de datos
2. **Augmented (Aumentado):** Usar esa información como contexto
3. **Generation (Generación):** Crear una respuesta basada en el contexto

**Sin RAG:**
```
Pregunta: "¿Cuál es el WVTR del PBAT?"
LLM: "No tengo acceso a datos específicos sobre WVTR de PBAT..."
```

**Con RAG:**
```
Pregunta: "¿Cuál es el WVTR del PBAT?"
Búsqueda: Encuentra registros de PBAT en la BD
LLM: "Según los datos, el PBAT tiene un WVTR de 4060 g/m²/day..."
```

### 1.2 ¿Qué es un Embedding?

Un **embedding** es una lista de números que representa el significado de un texto.

**Analogía:** Imagina que cada texto es un punto en un espacio de 1536 dimensiones. Textos con significado similar están cerca unos de otros.

```python
# Textos similares → Embeddings similares
texto_a = "PBAT tiene WVTR de 4060"
embedding_a = [0.23, -0.15, 0.89, ...]  # 1536 números

texto_b = "PBAT tiene WVTR de 4060 g/m²/day"
embedding_b = [0.24, -0.14, 0.88, ...]  # Muy similar a A

# Textos diferentes → Embeddings diferentes
texto_c = "El cielo es azul"
embedding_c = [-0.89, 0.45, -0.12, ...]  # Muy diferente
```

**Modelo de embedding:** `text-embedding-3-small` de OpenAI
- Dimensión: 1536
- Costo: $0.02 por 1M tokens
- Velocidad: Muy rápido

### 1.3 ¿Qué es pgvector?

**pgvector** es una extensión de PostgreSQL que permite:
1. Almacenar vectores (embeddings) en tablas
2. Calcular distancias entre vectores
3. Encontrar los vectores más similares

**Analogía:** Es como un índice de libros, pero en lugar de buscar por palabras clave, busca por "significado similar".

```sql
-- Sin pgvector: Búsqueda por palabras clave
SELECT * FROM wvtr_data WHERE polymer ILIKE '%PBAT%';

-- Con pgvector: Búsqueda por similitud semántica
SELECT * FROM wvtr_data 
ORDER BY embedding <-> '[0.23, -0.15, ...]'::vector 
LIMIT 5;
```

### 1.4 Flujo Completo de RAG

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    FLUJO RAG - PASO A PASO                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  FASE 1: INGESTA (Una vez)                                                │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐│
│  │  Datos      │    │  Modelo de  │    │  pgvector   │    │ PostgreSQL  ││
│  │  WVTR       │───→│  Embeddings │───→│  (almacena) │───→│  (datos)    ││
│  │  (JSON)     │    │  (OpenAI)   │    │             │    │             ││
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘│
│                                                                             │
│  FASE 2: CONSULTA (Cada vez que el usuario pregunta)                       │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐│
│  │  Pregunta   │    │  Modelo de  │    │  pgvector   │    │  LLM        ││
│  │  del        │───→│  Embeddings │───→│  (búsqueda) │───→│  (genera    ││
│  │  usuario    │    │  (OpenAI)   │    │             │    │  respuesta) ││
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘│
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Configurar el Entorno

### 2.1 Instalar pgvector

**Paso 1: Verificar PostgreSQL**
```bash
# Conectar a PostgreSQL
psql -U postgres

# Verificar versión
SELECT version();

# Salir
\q
```

**Paso 2: Instalar pgvector**
```bash
# En Ubuntu/Debian
sudo apt-get install postgresql-14-pgvector

# En macOS (con Homebrew)
brew install pgvector

# En Windows
# Descargar desde https://github.com/pgvector/pgvector/releases
```

**Paso 3: Habilitar la extensión**
```sql
-- Conectar a la base de datos
psql -U postgres -d polymers_wvtr

-- Crear extensión
CREATE EXTENSION IF NOT EXISTS vector;

-- Verificar
SELECT * FROM pg_extension WHERE extname = 'vector';
```

### 2.2 Modificar la Tabla

**Paso 1: Agregar columna de embeddings**
```sql
-- Agregar columna vectorial
ALTER TABLE wvtr_data 
ADD COLUMN IF NOT EXISTS embedding vector(1536);

-- Verificar estructura
\d wvtr_data
```

**Paso 2: Crear índice para búsqueda rápida**
```sql
-- Crear índice IVFFlat (para datasets medianos)
CREATE INDEX IF NOT EXISTS idx_wvtr_embedding 
ON wvtr_data 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 10);

-- Verificar índice
\di idx_wvtr_embedding
```

**Explicación del índice:**
- `ivfflat`: Algoritmo de índice (Inverted File with Flat quantization)
- `vector_cosine_ops`: Operador de similitud coseno
- `lists = 10`: Número de clusters (ajustar según tamaño de datos)

---

## 3. Crear el Módulo de Embeddings

### 3.1 Archivo: `src/embeddings.py`

```python
"""
embeddings.py - Generación de Embeddings para RAG
==================================================
Este módulo genera representaciones vectoriales (embeddings)
de los datos WVTR para búsquedas semánticas.

Conceptos clave:
- Embedding: Lista de números que representa el significado del texto
- text-embedding-3-small: Modelo de OpenAI para generar embeddings
- Dimensión 1536: Cada embedding tiene 1536 números
"""

from openai import OpenAI
from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL

# ============================================
# CONFIGURACIÓN DEL CLIENTE DE EMBEDDINGS
# ============================================

# Cliente de OpenAI para generar embeddings
embedding_client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url=OPENROUTER_BASE_URL
)

# Modelo de embeddings a utilizar
EMBEDDING_MODEL = "text-embedding-3-small"


def get_embedding(text: str) -> list:
    """
    Genera un embedding para un texto dado.
    
    ¿Qué hace?
    1. Toma un texto (ej: "PBAT tiene WVTR de 4060")
    2. Lo envía al modelo de embedding
    3. Retorna una lista de 1536 números
    
    Args:
        text: Texto a convertir en embedding
        
    Returns:
        list: Vector de 1536 dimensiones
        
    Ejemplo:
        >>> get_embedding("PBAT: 4060 g/m²/day")
        [0.023, -0.156, 0.089, ..., 0.045]  # 1536 números
    """
    try:
        response = embedding_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        raise Exception(f"Error generando embedding: {e}")


def generate_record_text(record: dict) -> str:
    """
    Genera texto representativo para un registro WVTR.
    
    ¿Qué hace?
    Convierte un registro de la base de datos en un texto
    que el modelo de embedding puede procesar.
    
    Args:
        record: Diccionario con datos del registro
        Ej: {
            "polymer": "PBAT",
            "wvtr_value": 4060,
            "wvtr_units": "g/m²/day",
            "temperature": "25°C",
            "rh": "50%",
            "article_title": "Improved barrier properties..."
        }
        
    Returns:
        str: Texto formateado
        
    Ejemplo:
        >>> record = {"polymer": "PBAT", "wvtr_value": 4060}
        >>> generate_record_text(record)
        "Polymer: PBAT | WVTR: 4060 g/m²/day | Source: Improved barrier..."
    """
    parts = []
    
    if record.get("polymer"):
        parts.append(f"Polymer: {record['polymer']}")
    
    if record.get("wvtr_value"):
        units = record.get("wvtr_units", "")
        parts.append(f"WVTR: {record['wvtr_value']} {units}")
    
    if record.get("temperature"):
        parts.append(f"Temperature: {record['temperature']}")
    
    if record.get("rh"):
        parts.append(f"Relative Humidity: {record['rh']}")
    
    if record.get("article_title"):
        title = record['article_title'][:100]
        parts.append(f"Source: {title}")
    
    return " | ".join(parts)


def generate_embedding_for_record(record: dict) -> list:
    """
    Genera embedding para un registro WVTR completo.
    
    Args:
        record: Diccionario con todos los datos del registro
        
    Returns:
        list: Vector embedding de 1536 dimensiones
    """
    text = generate_record_text(record)
    return get_embedding(text)
```

### 3.2 Explicación del Código

**Línea por línea:**

```python
# 1. Importar cliente de OpenAI
from openai import OpenAI

# 2. Crear cliente para embeddings
embedding_client = OpenAI(
    api_key=OPENROUTER_API_KEY,  # Tu API key
    base_url=OPENROUTER_BASE_URL  # URL de OpenRouter
)

# 3. Función para generar embedding
def get_embedding(text: str) -> list:
    # Enviar texto al modelo
    response = embedding_client.embeddings.create(
        model="text-embedding-3-small",  # Modelo de embedding
        input=text  # Texto a procesar
    )
    # Retornar el primer embedding
    return response.data[0].embedding
```

**¿Por qué 1536 dimensiones?**
- Es el tamaño del vector que genera `text-embedding-3-small`
- Más dimensiones = más precisión, pero más espacio
- 1536 es un buen balance para la mayoría de casos

---

## 4. Crear el Módulo RAG

### 4.1 Archivo: `src/rag.py`

```python
"""
rag.py - Motor de RAG para Datos WVTR
======================================
Este módulo implementa la lógica completa de RAG:
1. Recibir una pregunta del usuario
2. Buscar registros similares en la BD
3. Generar una respuesta basada en los datos

Flujo:
Pregunta → Embedding → Búsqueda → Contexto → LLM → Respuesta
"""

import json
from openai import OpenAI
from database import get_connection, release_connection
from embeddings import get_embedding, generate_record_text
from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL

# ============================================
# CONFIGURACIÓN DEL CLIENTE LLM
# ============================================

llm_client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url=OPENROUTER_BASE_URL
)

LLM_MODEL = "openai/gpt-5.6-luna"


def search_similar(query: str, limit: int = 5) -> list:
    """
    Busca registros similares usando embedding similarity.
    
    ¿Qué hace?
    1. Convierte la pregunta en un embedding
    2. Busca en pgvector los embeddings más similares
    3. Retorna los registros correspondientes
    
    Args:
        query: Pregunta del usuario
        limit: Número de resultados a retornar
        
    Returns:
        list: Lista de diccionarios con registros similares
    """
    # 1. Generar embedding de la pregunta
    query_embedding = get_embedding(query)
    
    # 2. Buscar en pgvector
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT 
            id, article_title, doi, polymer, 
            wvtr_value, wvtr_units, temperature, 
            rh, thickness, test_method
        FROM wvtr_data
        ORDER BY embedding <-> %s
        LIMIT %s
    """, (str(query_embedding), limit))
    
    results = cur.fetchall()
    release_connection(conn)
    
    # 3. Formatear resultados
    formatted = []
    for row in results:
        formatted.append({
            "id": row[0],
            "article_title": row[1],
            "doi": row[2],
            "polymer": row[3],
            "wvtr_value": row[4],
            "wvtr_units": row[5],
            "temperature": row[6],
            "rh": row[7],
            "thickness": row[8],
            "test_method": row[9]
        })
    
    return formatted


def format_context(results: list) -> str:
    """
    Formatea resultados como contexto para el LLM.
    
    Args:
        results: Lista de resultados de búsqueda
        
    Returns:
        str: Contexto formateado
    """
    context_parts = []
    
    for i, r in enumerate(results, 1):
        part = f"""
{i}. Polymer: {r['polymer']}
   WVTR: {r['wvtr_value']} {r['wvtr_units']}
   Temperature: {r.get('temperature', 'N/A')}
   RH: {r.get('rh', 'N/A')}
   Source: {r['article_title'][:80]}...
   DOI: {r.get('doi', 'N/A')}
"""
        context_parts.append(part)
    
    return "\n".join(context_parts)


def generate_answer(question: str, context: str) -> str:
    """
    Genera respuesta usando LLM con contexto RAG.
    
    Args:
        question: Pregunta del usuario
        context: Contexto de la búsqueda
        
    Returns:
        str: Respuesta generada
    """
    system_prompt = """Eres un experto en ciencia de polímeros y barrera de empaque.

Tu tarea es responder preguntas sobre Water Vapor Transmission Rate (WVTR) basándote 
únicamente en los datos proporcionados como contexto.

Reglas:
1. Responde SOLO basándote en los datos del contexto
2. Cita las fuentes (artículos) cuando sea posible
3. Si no hay datos suficientes, di "No tengo datos suficientes para responder"
4. Usa unidades y condiciones exactas de los datos
5. Si compara polímeros, incluye valores numéricos"""

    user_prompt = f"""
Contexto de la base de datos WVTR:
{context}

Pregunta del usuario: {question}

Respuesta:"""

    response = llm_client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0
    )
    
    return response.choices[0].message.content


def rag_query(question: str, limit: int = 5) -> dict:
    """
    Función principal de RAG.
    
    Args:
        question: Pregunta del usuario
        limit: Número de resultados a buscar
        
    Returns:
        dict: {answer, sources, context_used}
    """
    # 1. Buscar registros similares
    results = search_similar(question, limit)
    
    # 2. Formatear contexto
    context = format_context(results)
    
    # 3. Generar respuesta
    answer = generate_answer(question, context)
    
    # 4. Retornar respuesta + fuentes
    return {
        "answer": answer,
        "sources": results,
        "context_used": context
    }
```

### 4.2 Explicación del Código RAG

**Flujo completo:**

```
1. Usuario pregunta: "¿Cuál es el WVTR del PBAT?"
                    │
                    ▼
2. search_similar() genera embedding de la pregunta
                    │
                    ▼
3. pgvector busca los 5 registros más similares
                    │
                    ▼
4. format_context() convierte resultados en texto
                    │
                    ▼
5. generate_answer() envía contexto + pregunta al LLM
                    │
                    ▼
6. LLM genera respuesta fundamentada
                    │
                    ▼
7. Retornar: {answer, sources, context_used}
```

**¿Por qué usar GPT-5.6 Luna para respuestas?**
- Es bueno entendiendo contexto científico
- Genera respuestas claras y precisas
- Puede comparar datos numéricos

**¿Por qué temperature=0?**
- Respuestas más consistentes y precisas
- Menos "creatividad" (que no queremos en datos científicos)
- Mejor para extracción de datos

---

## 5. Crear la API

### 5.1 Archivo: `api/server.py`

```python
"""
server.py - API REST para RAG WVTR
===================================
Expone endpoints HTTP para consultas RAG.

Endpoints:
- GET /: Información de la API
- POST /query: Consulta RAG completa
- POST /search: Solo búsqueda de registros
- GET /health: Verificar estado
"""

from fastapi import FastAPI
from pydantic import BaseModel
from rag import rag_query, search_similar

# ============================================
# CONFIGURACIÓN DE LA API
# ============================================

app = FastAPI(
    title="WVTR RAG API",
    description="API para consultas RAG sobre datos WVTR",
    version="1.0.0"
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
        "limit": 5
    }
    """
    question: str
    limit: int = 5


class QueryResponse(BaseModel):
    """
    Modelo para respuestas RAG.
    
    Ejemplo:
    {
        "answer": "Según los datos...",
        "sources": [{"polymer": "PBAT", ...}],
        "context_used": "1. Polymer: PBAT..."
    }
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
    
    Uso: POST /query
    Body: {"question": "¿Cuál es el WVTR del PBAT?"}
    Retorna: Respuesta + fuentes + contexto
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
    
    Uso: POST /search
    Body: {"question": "PBAT", "limit": 5}
    Retorna: Solo los registros encontrados
    """
    results = search_similar(request.question, request.limit)
    return {"results": results, "count": len(results)}


@app.get("/health")
def health_check():
    """Verificar estado de la API y base de datos."""
    from database import get_connection, release_connection
    
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM wvtr_data")
        count = cur.fetchone()[0]
        release_connection(conn)
        
        return {
            "status": "healthy",
            "database": "connected",
            "records": count
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### 5.2 Explicación de la API

**¿Qué es FastAPI?**
- Framework moderno para crear APIs en Python
- Fácil de usar y muy rápido
- Genera documentación automática

**¿Qué es Pydantic?**
- Librería para validar datos
- Asegura que las peticiones tengan el formato correcto
- Genera errores claros si falta algo

**Endpoints explicados:**

| Método | Ruta | Descripción | Ejemplo |
|--------|------|-------------|---------|
| GET | `/` | Info de la API | `curl http://localhost:8000/` |
| POST | `/query` | Consulta RAG completa | `curl -X POST .../query -d '{"question": "..."}'` |
| POST | `/search` | Solo búsqueda | `curl -X POST .../search -d '{"question": "..."}'` |
| GET | `/health` | Estado | `curl http://localhost:8000/health` |

---

## 6. Probar el Sistema

### 6.1 Script de Prueba: `scripts/test_rag.py`

```python
"""
test_rag.py - Pruebas del sistema RAG
======================================
Ejecutar: python scripts/test_rag.py
"""

import sys
sys.path.insert(0, 'src')

from rag import rag_query, search_similar

def test_search():
    """Prueba la búsqueda de registros."""
    print("=" * 60)
    print("PRUEBA 1: Búsqueda de registros")
    print("=" * 60)
    
    results = search_similar("PBAT", limit=3)
    
    print(f"\nResultados encontrados: {len(results)}")
    for i, r in enumerate(results, 1):
        print(f"\n{i}. {r['polymer']}")
        print(f"   WVTR: {r['wvtr_value']} {r['wvtr_units']}")
        print(f"   Fuente: {r['article_title'][:50]}...")

def test_rag():
    """Prueba la consulta RAG completa."""
    print("\n" + "=" * 60)
    print("PRUEBA 2: Consulta RAG completa")
    print("=" * 60)
    
    questions = [
        "¿Cuál es el WVTR del PBAT?",
        "¿Qué polímeros tienen mejor barrera que LDPE?",
        "Compara PHBV con PHBV/clay nanocomposite"
    ]
    
    for question in questions:
        print(f"\nPregunta: {question}")
        print("-" * 40)
        
        result = rag_query(question)
        
        print(f"\nRespuesta:\n{result['answer']}")
        print(f"\nFuentes: {len(result['sources'])} registros")

if __name__ == "__main__":
    test_search()
    test_rag()
```

---

## 7. Ejemplos de Uso

### 7.1 Desde Python

```python
from rag import rag_query

# Ejemplo 1: Consulta simple
result = rag_query("¿Cuál es el WVTR del PBAT?")
print(result["answer"])

# Ejemplo 2: Comparación
result = rag_query("Compara PHBV con PHBV/clay nanocomposite")
print(result["answer"])

# Ejemplo 3: Condiciones específicas
result = rag_query("¿Qué polímeros tienen WVTR menor a 100 g/m²/day?")
print(result["answer"])
```

### 7.2 Desde curl

```bash
# Consulta RAG completa
curl -X POST http://localhost:8000/query \
     -H "Content-Type: application/json" \
     -d '{"question": "¿Cuál es el WVTR del PBAT?"}'

# Solo búsqueda
curl -X POST http://localhost:8000/search \
     -H "Content-Type: application/json" \
     -d '{"question": "PBAT", "limit": 3}'

# Verificar estado
curl http://localhost:8000/health
```

### 7.3 Desde DBeaver

```sql
-- Ver embeddings generados
SELECT id, polymer, wvtr_value, 
       embedding::vector(10) as embedding_preview
FROM wvtr_data 
WHERE embedding IS NOT NULL
LIMIT 5;

-- Búsqueda manual por similitud
SELECT id, polymer, wvtr_value,
       1 - (embedding <=> '[0.23, -0.15, ...]'::vector) as similarity
FROM wvtr_data
ORDER BY embedding <=> '[0.23, -0.15, ...]'::vector
LIMIT 5;
```

---

## 8. Costos

### 8.1 Costo de Embeddings

| Concepto | Costo |
|----------|-------|
| Generar embedding (una vez) | $0.0000155 |
| Consulta RAG | ~$0.0001 |
| 100 consultas | ~$0.01 |
| 1,000 consultas | ~$0.10 |

### 8.2 Costo de LLM (GPT-5.6 Luna)

| Concepto | Costo |
|----------|-------|
| Input (contexto + pregunta) | $0.20 / 1M tokens |
| Output (respuesta) | $1.20 / 1M tokens |
| Consulta típica | ~$0.003 |

### 8.3 Costo Total Estimado

| Escenario | Costo |
|-----------|-------|
| Desarrollo y pruebas | ~$0.10 |
| Uso mensual (100 consultas) | ~$0.30 |
| Uso mensual (1,000 consultas) | ~$3.00 |

---

## 9. Solución de Problemas

### 9.1 Error: "pgvector not installed"

```sql
-- Verificar si pgvector está instalado
SELECT * FROM pg_extension WHERE extname = 'vector';

-- Si no aparece, instalarlo
CREATE EXTENSION IF NOT EXISTS vector;
```

### 9.2 Error: "column embedding does not exist"

```sql
-- Agregar columna de embeddings
ALTER TABLE wvtr_data 
ADD COLUMN IF NOT EXISTS embedding vector(1536);
```

### 9.3 Error: "index already exists"

```sql
-- El índice ya existe, no hacer nada
-- O eliminar y recrear:
DROP INDEX IF EXISTS idx_wvtr_embedding;
CREATE INDEX idx_wvtr_embedding ON wvtr_data 
USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
```

### 9.4 Embeddings no generados

```python
# Verificar que los embeddings se generaron
from database import get_connection, release_connection

conn = get_connection()
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM wvtr_data WHERE embedding IS NOT NULL")
count = cur.fetchone()[0]
release_connection(conn)

print(f"Registros con embedding: {count}")
```

---

## 10. Siguientes Pasos

### 10.1 Mejoras Futuras

1. **Agregar más fuentes:** PDFs, papers, bases de datos externas
2. **Mejorar el prompt:** Ajustar instrucciones para mejores respuestas
3. **Agregar historial:** Guardar preguntas y respuestas anteriores
4. **Interfaz gráfica:** Crear un chat web interactivo
5. **Evaluación:** Medir precisión de las respuestas

### 10.2 Recursos Adicionales

- [Documentación de pgvector](https://github.com/pgvector/pgvector)
- [Documentación de OpenAI Embeddings](https://platform.openai.com/docs/guides/embeddings)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Paper: Retrieval-Augmented Generation](https://arxiv.org/abs/2005.11401)

---

## 11. Resumen

### Archivos Creados

| Archivo | Descripción |
|---------|-------------|
| `src/embeddings.py` | Generación de embeddings |
| `src/rag.py` | Motor RAG completo |
| `api/server.py` | API REST |
| `scripts/setup_pgvector.sql` | Configuración pgvector |
| `scripts/test_rag.py` | Pruebas del sistema |

### Dependencias

```txt
pgvector>=0.2.0
fastapi>=0.104.0
uvicorn>=0.24.0
openai>=1.3.0
```

### Comandos Útiles

```bash
# Configurar pgvector
psql -U postgres -d polymers_wvtr -f scripts/setup_pgvector.sql

# Generar embeddings
python scripts/generate_embeddings.py

# Ejecutar API
python api/server.py

# Probar RAG
python scripts/test_rag.py
```

---

*Última actualización: Septiembre 2026*
