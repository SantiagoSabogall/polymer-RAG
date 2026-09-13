"""
rag.py - Motor de RAG para Datos WVTR
======================================
Implementa Retrieval-Augmented Generation para
consultas en lenguaje natural sobre datos WVTR.

Flujo:
Pregunta → Embedding → Búsqueda → Contexto → LLM → Respuesta
"""

import re
from openai import OpenAI
try:
    from .database import get_connection, release_connection
    from .embeddings import get_embedding, generate_record_text
    from .config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
except ImportError:
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


def _extract_temperature(query: str) -> str | None:
    """
    Extrae una temperatura de la query del usuario.
    
    Soporta: 38°C, 38 grados, 38C, temperatura 38, a 38, etc.
    """
    patterns = [
        r'(\d+)\s*°\s*C',
        r'(\d+)\s*(?:grados?\s*(?:centígrados?)?)',
        r'(\d+)\s*celsius',
        r'(\d+)\s*C(?:\s|$)',
        r'temperatura\s+(?:de\s+)?(\d+)',
        r'a\s+(\d+)\s*°?\s*C',
    ]
    for pat in patterns:
        match = re.search(pat, query, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def search_similar(query: str, limit: int = 10) -> list:
    """
    Busca registros similares usando embedding similarity.
    Si la query menciona una temperatura, tambien busca por esa temperatura.
    Si no menciona temperatura, incluye diversidad de temperaturas.
    
    Args:
        query: Pregunta del usuario
        limit: Número de resultados a retornar
        
    Returns:
        list: Lista de diccionarios con registros similares
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        # 1. Buscar por embedding similarity (pedir más para tener margen)
        query_embedding = get_embedding(query)
        fetch_limit = limit * 2
        
        cur.execute("""
            SELECT 
                id, article_title, doi, polymer, 
                wvtr_value, wvtr_units, temperature, 
                rh, thickness, test_method
            FROM wvtr_data
            WHERE embedding IS NOT NULL
            ORDER BY embedding <-> %s
            LIMIT %s
        """, (str(query_embedding), fetch_limit))
        
        embedding_results = cur.fetchall()
        
        # 2. Detectar si se menciona una temperatura en la query
        temp_value = _extract_temperature(query)
        temperature_results = []
        
        if temp_value:
            cur.execute("""
                SELECT 
                    id, article_title, doi, polymer, 
                    wvtr_value, wvtr_units, temperature, 
                    rh, thickness, test_method
                FROM wvtr_data
                WHERE temperature ILIKE %s
            """, (f'%{temp_value}%',))
            temperature_results = cur.fetchall()
        
        # 3. Si no se pidió temperatura explícita, asegurar diversidad
        if not temp_value:
            temps_in_results = set()
            for row in embedding_results:
                t = row[6]
                if t:
                    num = re.search(r'(\d+)', t)
                    if num:
                        temps_in_results.add(num.group(1))
            
            # Obtener todas las temperaturas disponibles en la BD
            cur.execute("""
                SELECT DISTINCT regexp_replace(temperature, '[^0-9]', '', 'g') as temp_num
                FROM wvtr_data
                WHERE temperature IS NOT NULL
                  AND temperature != ''
                  AND embedding IS NOT NULL
            """)
            all_db_temps = {row[0] for row in cur.fetchall() if row[0]}
            
            # Calcular temperaturas faltantes
            missing_temps = all_db_temps - temps_in_results
            
            # Si faltan temperaturas, buscar registros de esas temperaturas
            if missing_temps:
                for mt in missing_temps:
                    cur.execute("""
                        SELECT 
                            id, article_title, doi, polymer, 
                            wvtr_value, wvtr_units, temperature, 
                            rh, thickness, test_method
                        FROM wvtr_data
                        WHERE embedding IS NOT NULL
                          AND temperature ILIKE %s
                        ORDER BY embedding <-> %s
                        LIMIT 2
                    """, (f'%{mt}%', str(query_embedding)))
                    
                    for row in cur.fetchall():
                        if row[0] not in {r[0] for r in temperature_results}:
                            temperature_results.append(row)
        
        cur.close()
    finally:
        release_connection(conn)
    
    # 4. Combinar resultados (evitar duplicados)
    seen_ids = set()
    all_results = []
    
    # Primero los de temperatura (si hay)
    for row in temperature_results:
        if row[0] not in seen_ids:
            seen_ids.add(row[0])
            all_results.append(row)
    
    # Luego los de embedding
    for row in embedding_results:
        if row[0] not in seen_ids:
            seen_ids.add(row[0])
            all_results.append(row)
    
    # 5. Formatear resultados
    formatted = []
    for row in all_results[:limit]:
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
    conn = None
    try:
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
        
        cur.close()
    finally:
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
    system_prompt = """Eres un asistente experto en ciencia de polimeros y barrera de empaque.
Tu nombre es WVTR Assistant.

Responde de forma natural y amigable, como si hablaras con un colega investigador.

## Como responder:

Cuando te pregunten por un polimero especifico:
1. Saluda de forma natural
2. Presenta los datos en una lista organizada y clara
3. Incluye TODAS las condiciones disponibles (temperatura, RH, espesor, metodo)
4. Cita la fuente (articulo y DOI si esta disponible)
5. Ofrece ayuda adicional al final

IMPORTANTE: Siempre muestra la fuente y el DOI por SEPARADO en cada registro, asi:
- Fuente: [nombre del articulo]
- DOI: [numero DOI]

Ejemplo de respuesta ideal:
"Hola! Encontre estos datos de PBAT en la base de datos:

**PBAT** (puro)
- WVTR: 4060 x 10^-13 g.m/m2.s.Pa
- Temperatura: 25 C
- HR: 5% y 95%
- Espesor: 60 um
- Metodo: ASTM E 96
- Fuente: Improved barrier properties...
- DOI: 10.1002/app.53855

**PBAT/Ag2O (10 wt%)**
- WVTR: 57.4 g m-2 per 24 h
- Temperatura: 25 C
- HR: 50%
- Espesor: 0.08-0.1 mm
- Metodo: ASTM E-987
- Fuente: Antimicrobial, mechanical, barrier...
- DOI: 10.1002/pat.4089

Nota: Si la fuente y DOI son iguales para varios registros, repitelos en CADA registro. No los agrupes.

Cuando te pregunten para comparar:
- Organiza por polimero
- Resalta las diferencias clave con negritas
- Da una conclusion breve

Cuando busquen por condiciones especificas:
- Lista todos los polimeros que coincidan
- Incluye las condiciones exactas

Reglas:
1. Responde SOLO basandote en los datos del contexto
2. Usa lenguaje natural pero preciso
3. Si no hay datos, di "No encontre datos suficientes"
4. Siempre incluye unidades exactas
5. Responde en el idioma de la pregunta
6. Usa markdown para organizar (negritas, listas)
7. SIEMPRE muestra Fuente y DOI por separado en CADA registro"""

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
