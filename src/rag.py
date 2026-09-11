"""
rag.py - Motor de RAG para Datos WVTR
======================================
Implementa Retrieval-Augmented Generation para
consultas en lenguaje natural sobre datos WVTR.

Flujo:
Pregunta → Embedding → Búsqueda → Contexto → LLM → Respuesta
"""

import json
from openai import OpenAI
from database import get_connection, release_connection
from embeddings import get_embedding, generate_record_text
from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL


# Cliente LLM para generar respuestas
llm_client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url=OPENROUTER_BASE_URL
)

# Modelo LLM para generar respuestas
LLM_MODEL = "openai/gpt-5.6-luna"


def search_similar(query: str, limit: int = 10) -> list:
    """
    Busca registros similares usando embedding similarity.
    
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


def get_db_stats() -> dict:
    """
    Obtiene estadísticas de la base de datos.
    
    Returns:
        dict: Estadísticas de la BD
    """
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM wvtr_data")
    total_records = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(DISTINCT polymer) FROM wvtr_data")
    total_polymers = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(DISTINCT article_title) FROM wvtr_data")
    total_articles = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM wvtr_data WHERE doi IS NOT NULL")
    records_with_doi = cur.fetchone()[0]
    
    release_connection(conn)
    
    return {
        "total_records": total_records,
        "total_polymers": total_polymers,
        "total_articles": total_articles,
        "records_with_doi": records_with_doi
    }


def format_context(results: list, db_stats: dict = None) -> str:
    """
    Formatea resultados como contexto para el LLM.
    
    Args:
        results: Lista de resultados de búsqueda
        db_stats: Estadísticas de la base de datos
        
    Returns:
        str: Contexto formateado
    """
    context_parts = []
    
    # Agregar estadísticas de la BD al inicio
    if db_stats:
        stats_section = f"""INFORMACIÓN DE LA BASE DE DATOS:
- Total de registros WVTR: {db_stats['total_records']}
- Polímeros únicos: {db_stats['total_polymers']}
- Artículos únicos: {db_stats['total_articles']}
- Registros con DOI: {db_stats['records_with_doi']}

"""
        context_parts.append(stats_section)
    
    for i, r in enumerate(results, 1):
        part = f"""
{i}. Polymer: {r['polymer']}
   WVTR: {r['wvtr_value']} {r['wvtr_units']}
   Temperature: {r.get('temperature', 'N/A')}
   RH: {r.get('rh', 'N/A')}
   Thickness: {r.get('thickness', 'N/A')}
   Test Method: {r.get('test_method', 'N/A')}
   Source: {r['article_title'][:100]}
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
    system_prompt = """Eres un asistente experto en ciencia de polímeros y barrera de empaque.
Tu nombre es WVTR Assistant.

Responde de forma natural y amigable, como si hablaras con un colega investigador.

## Cómo responder:

Cuando te pregunten por un polímero específico:
1. Saluda de forma natural
2. Presenta los datos en una lista organizada y clara
3. Incluye TODAS las condiciones disponibles (temperatura, RH, espesor, método)
4. Cita la fuente (artículo y DOI si está disponible)
5. Ofrece ayuda adicional al final

Ejemplo de respuesta ideal:
"Hola! Encontré estos datos de PBAT en la base de datos:

**PBAT** (puro)
- WVTR: 4060 × 10⁻¹³ g·m/m²·s·Pa
- Temperatura: 25°C
- HR: 5% y 95%
- Espesor: 60 μm
- Método: ASTM E 96
- Fuente: Improved barrier properties... (DOI: 10.1002/app.53855)

¿Te gustaría que compare estos con otros polímeros?"

Cuando te pregunten para comparar:
- Organiza por polímero
- Resalta las diferencias clave con negritas
- Da una conclusión breve

Cuando busquen por condiciones específicas:
- Lista todos los polímeros que coincidan
- Incluye las condiciones exactas

Reglas:
1. Responde SOLO basándote en los datos del contexto
2. Usa lenguaje natural pero preciso
3. Si no hay datos, di "No encontré datos suficientes"
4. Siempre incluye unidades exactas
5. Responde en el idioma de la pregunta
6. Usa markdown para organizar (negritas, listas)"""

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


def format_natural_response(answer: str, sources: list) -> str:
    """
    Agrega información de fuentes al final de la respuesta.
    
    Args:
        answer: Respuesta generada por el LLM
        sources: Fuentes utilizadas
        
    Returns:
        str: Respuesta formateada con fuentes
    """
    if not sources:
        return answer
    
    # Agregar sección de fuentes al final
    sources_section = "\n\n---\n**Fuentes consultadas:**\n"
    for i, s in enumerate(sources[:5], 1):  # Mostrar solo las 5 principales
        doi = f" (DOI: {s['doi']})" if s.get('doi') else ""
        sources_section += f"{i}. {s['article_title'][:60]}...{doi}\n"
    
    return answer + sources_section


def rag_query(question: str, limit: int = 10) -> dict:
    """
    Función principal de RAG.
    
    Args:
        question: Pregunta del usuario
        limit: Número de resultados a buscar
        
    Returns:
        dict: {answer, sources, context_used}
    """
    # 1. Obtener estadísticas de la BD
    db_stats = get_db_stats()
    
    # 2. Buscar registros similares
    results = search_similar(question, limit)
    
    # 3. Formatear contexto con estadísticas
    context = format_context(results, db_stats)
    
    # 4. Generar respuesta
    answer = generate_answer(question, context)
    
    # 5. Formatear respuesta natural con fuentes
    formatted_answer = format_natural_response(answer, results)
    
    # 6. Retornar respuesta + fuentes
    return {
        "answer": formatted_answer,
        "sources": results,
        "context_used": context,
        "db_stats": db_stats
    }
