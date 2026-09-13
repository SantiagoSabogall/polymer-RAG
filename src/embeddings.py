"""
embeddings.py - Generación de Embeddings para RAG
==================================================
Genera representaciones vectoriales (embeddings) de los datos WVTR
para búsquedas semánticas usando pgvector.

Modelo: text-embedding-3-small (1536 dimensiones)
"""

from openai import OpenAI
try:
    from .config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
except ImportError:
    from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL


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
    
    Args:
        text: Texto a convertir en embedding
        
    Returns:
        list: Vector de 1536 dimensiones
    """
    try:
        response = embedding_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        raise Exception(f"Error generar embedding: {e}")


def generate_record_text(record: dict) -> str:
    """
    Genera texto representativo para un registro WVTR.
    
    Args:
        record: Diccionario con datos del registro
        
    Returns:
        str: Texto formateado para embedding
    """
    parts = []
    
    if record.get("polymer"):
        parts.append(f"Polymer: {record['polymer']}")
    
    if record.get("wvtr_value"):
        parts.append(f"WVTR: {record['wvtr_value']}")
    
    if record.get("wvtr_units"):
        parts.append(f"Units: {record['wvtr_units']}")
    
    if record.get("temperature"):
        parts.append(f"Temperature: {record['temperature']}")
    
    if record.get("rh"):
        parts.append(f"RH: {record['rh']}")
    
    if record.get("thickness"):
        parts.append(f"Thickness: {record['thickness']}")
    
    if record.get("article_title"):
        parts.append(f"Source: {record['article_title'][:100]}")
    
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
