"""
generate_embeddings.py - Genera embeddings para registros WVTR
==============================================================
Ejecutar: python scripts/generate_embeddings.py

Este script genera embeddings para todos los registros en wvtr_data
que no tengan embedding generado aún.
"""

import sys
from pathlib import Path

# Agregar src/ al path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from database import get_connection, release_connection
from embeddings import get_embedding, generate_record_text


def generate_all_embeddings():
    """Genera embeddings para todos los registros sin embedding."""
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        # Obtener registros sin embedding
        cur.execute("SELECT id, raw_json FROM wvtr_data WHERE embedding IS NULL")
        records = cur.fetchall()
        
        print(f"Registros sin embedding: {len(records)}")
        
        if not records:
            print("Todos los registros ya tienen embedding.")
            return
        
        for i, (record_id, raw_json) in enumerate(records, 1):
            try:
                # Generar texto para embedding
                text = generate_record_text(raw_json)
                
                # Generar embedding
                embedding = get_embedding(text)
                
                # Guardar en base de datos
                cur.execute(
                    "UPDATE wvtr_data SET embedding = %s WHERE id = %s",
                    (str(embedding), record_id)
                )
                
                print(f"  [{i}/{len(records)}] ID {record_id} - Embedding generado")
                
            except Exception as e:
                print(f"  [{i}/{len(records)}] ID {record_id} - ERROR: {e}")
        
        # Confirmar cambios
        conn.commit()
        
        print(f"\nTotal: {len(records)} embeddings generados")
    finally:
        release_connection(conn)


def verify_embeddings():
    """Verifica el estado de los embeddings."""
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT COUNT(*) FROM wvtr_data")
        total = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM wvtr_data WHERE embedding IS NOT NULL")
        with_embeddings = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM wvtr_data WHERE embedding IS NULL")
        without_embeddings = cur.fetchone()[0]
        
        cur.close()
    finally:
        release_connection(conn)
    
    print(f"Total registros: {total}")
    print(f"Con embedding: {with_embeddings}")
    print(f"Sin embedding: {without_embeddings}")


if __name__ == "__main__":
    print("=" * 50)
    print("GENERACIÓN DE EMBEDDINGS")
    print("=" * 50)
    
    print("\nEstado actual:")
    verify_embeddings()
    
    print("\nGenerando embeddings...")
    generate_all_embeddings()
    
    print("\nEstado final:")
    verify_embeddings()
