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
   Source: {r['article_title'][:80]}
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
5. Si comparas polímeros incluye valores numéricos
6. Si la consulta se hace en inglés responde en inglés"""

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


def rag_query(question: str, limit: int = 10) -> dict:
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
