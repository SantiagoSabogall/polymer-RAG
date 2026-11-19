"""
generate_embeddings.py - Genera embeddings para registros WVTR
==============================================================
Ejecutar: python scripts/generate_embeddings.py

Este script genera embeddings para todos los registros en wvtr_data
que no tengan embedding generado aún.
"""

import argparse
import sys
from pathlib import Path

# Agregar src/ al path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from database import get_connection, release_connection
from embeddings import get_embedding, generate_record_text


def generate_all_embeddings(force=False):
    """Genera embeddings para todos los registros sin embedding."""
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        if force:
            cur.execute("UPDATE wvtr_data SET embedding = NULL")
            conn.commit()
        # Obtener registros sin embedding
        cur.execute("SELECT id, article_title, polymer, wvtr_value, wvtr_units, temperature, rh, thickness, test_method FROM wvtr_data WHERE embedding IS NULL ORDER BY id")
        records = cur.fetchall()
        
        print(f"Registros sin embedding: {len(records)}")
        
        if not records:
            print("Todos los registros ya tienen embedding.")
            return
        
        generated = 0
        conn.commit()
        columns = ("id", "article_title", "polymer", "wvtr_value", "wvtr_units",
                   "temperature", "rh", "thickness", "test_method")
        for i, row in enumerate(records, 1):
            record_id = row[0]
            try:
                # Generar texto para embedding
                text = generate_record_text(dict(zip(columns, row)))
                
                # Generar embedding
                embedding = get_embedding(text)
                
                # Guardar en base de datos
                cur.execute(
                    "UPDATE wvtr_data SET embedding = %s WHERE id = %s",
                    (str(embedding), record_id)
                )
                
                conn.commit()
                generated += 1
                print(f"  [{i}/{len(records)}] ID {record_id} - Embedding generado")
                
            except Exception as e:
                conn.rollback()
                print(f"  [{i}/{len(records)}] ID {record_id} - ERROR: {e}")
        
        # Confirmar cambios
        conn.commit()
        
        print(f"\nTotal: {generated} embeddings generados")
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Genera embeddings en PostgreSQL")
    parser.add_argument("--force", action="store_true",
                        help="Regenera todos los embeddings, no solo los ausentes")
    args = parser.parse_args(argv)
    print("=" * 50)
    print("GENERACIÓN DE EMBEDDINGS")
    print("=" * 50)
    
    print("\nEstado actual:")
    verify_embeddings()
    
    print("\nGenerando embeddings...")
    generate_all_embeddings(force=args.force)
    
    print("\nEstado final:")
    verify_embeddings()


if __name__ == "__main__":
    main()
