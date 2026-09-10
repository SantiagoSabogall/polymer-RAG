"""
explore_db.py - Exploración de la base de datos PostgreSQL
==========================================================
Uso: python explore_db.py

Funciones:
1. Muestra estadísticas básicas de la BD
2. Lista todos los registros formateados
3. Estadísticas por polímero
4. Exporta a CSV
"""

import sys
import csv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import PG_HOST, PG_DATABASE, PG_USER, PG_PASSWORD
from database import get_connection, get_stats


def explore():
    """Explora la base de datos y muestra información completa."""
    try:
        conn = get_connection()
        cur = conn.cursor()
    except Exception as e:
        print(f"Error conectando a PostgreSQL: {e}")
        print("Verifica que PostgreSQL esté corriendo en localhost:5432")
        return

    # ============================================
    # 1. ESTADÍSTICAS BÁSICAS
    # ============================================
    stats = get_stats(conn)
    print("=" * 70)
    print("ESTADÍSTICAS DE LA BASE DE DATOS: polymers_wvtr")
    print("=" * 70)
    print(f"  Total registros WVTR:  {stats['total_registros']}")
    print(f"  Artículos únicos:      {stats['dois_unicos']}")
    print(f"  Polímeros únicos:      {stats['polimeros_unicos']}")
    print("=" * 70)

    # ============================================
    # 2. TODOS LOS DATOS
    # ============================================
    cur.execute("""
        SELECT 
            id, article_title, doi, polymer, 
            wvtr_value, wvtr_units, temperature, 
            rh, thickness, test_method
        FROM wvtr_data 
        ORDER BY article_title, polymer
    """)
    rows = cur.fetchall()

    print(f"\n{'=' * 70}")
    print(f"TODOS LOS DATOS ({len(rows)} registros)")
    print(f"{'=' * 70}")
    print(f"{'ID':<4} {'Polímero':<28} {'WVTR':<10} {'Unidades':<18} {'Temp':<8} {'RH':<8}")
    print("-" * 70)

    for row in rows:
        rid, title, doi, polymer, wvtr, units, temp, rh, thick, method = row
        polymer_short = (polymer or "N/A")[:28]
        units_short = (units or "N/A")[:18]
        temp_short = (temp or "—")[:8]
        rh_short = (rh or "—")[:8]
        print(f"{rid:<4} {polymer_short:<28} {wvtr:<10} {units_short:<18} {temp_short:<8} {rh_short:<8}")

    # ============================================
    # 3. ESTADÍSTICAS POR POLÍMERO
    # ============================================
    cur.execute("""
        SELECT 
            polymer,
            COUNT(*) as veces,
            ROUND(AVG(wvtr_value)::numeric, 2) as promedio,
            ROUND(MIN(wvtr_value)::numeric, 2) as minimo,
            ROUND(MAX(wvtr_value)::numeric, 2) as maximo,
            STRING_AGG(DISTINCT wvtr_units, ', ') as unidades
        FROM wvtr_data 
        WHERE wvtr_value IS NOT NULL
        GROUP BY polymer 
        ORDER BY veces DESC
    """)
    stats_poly = cur.fetchall()

    print(f"\n{'=' * 70}")
    print("ESTADÍSTICAS POR POLÍMERO")
    print(f"{'=' * 70}")
    print(f"{'Polímero':<30} {'#':<5} {'Promedio':<12} {'Mínimo':<12} {'Máximo':<12}")
    print("-" * 70)

    for row in stats_poly:
        polymer, veces, promedio, minimo, maximo, unidades = row
        print(f"{polymer[:30]:<30} {veces:<5} {promedio:<12} {minimo:<12} {maximo:<12}")

    # ============================================
    # 4. ESTADÍSTICAS POR ARTÍCULO
    # ============================================
    cur.execute("""
        SELECT 
            article_title,
            COUNT(*) as registros,
            STRING_AGG(DISTINCT polymer, ', ') as polimeros
        FROM wvtr_data 
        GROUP BY article_title 
        ORDER BY registros DESC
    """)
    stats_art = cur.fetchall()

    print(f"\n{'=' * 70}")
    print("ESTADÍSTICAS POR ARTÍCULO")
    print(f"{'=' * 70}")

    for row in stats_art:
        title, registros, polimeros = row
        print(f"\n  [{registros} registros] {title}")
        print(f"    Polímeros: {polimeros}")

    cur.close()
    conn.close()


def export_csv():
    """Exporta todos los datos a un archivo CSV."""
    try:
        conn = get_connection()
        cur = conn.cursor()
    except Exception as e:
        print(f"Error conectando a PostgreSQL: {e}")
        return

    cur.execute("SELECT * FROM wvtr_data ORDER BY id")
    rows = cur.fetchall()
    cols = [desc[0] for desc in cur.description]

    output_file = Path(__file__).parent.parent / "temp" / "wvtr_data_export.csv"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(rows)

    print(f"\nExportado a: {output_file}")
    print(f"Registros: {len(rows)}")

    cur.close()
    conn.close()


def search_polymer(polymer_name: str):
    """Busca registros por nombre de polímero."""
    try:
        conn = get_connection()
        cur = conn.cursor()
    except Exception as e:
        print(f"Error conectando a PostgreSQL: {e}")
        return

    cur.execute("""
        SELECT 
            id, article_title, polymer, 
            wvtr_value, wvtr_units, temperature, rh
        FROM wvtr_data 
        WHERE polymer ILIKE %s
        ORDER BY wvtr_value
    """, (f"%{polymer_name}%",))
    rows = cur.fetchall()

    print(f"\n{'=' * 70}")
    print(f"RESULTADOS PARA: {polymer_name}")
    print(f"{'=' * 70}")

    if not rows:
        print("  No se encontraron registros.")
    else:
        print(f"  Encontrados: {len(rows)} registros\n")
        for row in rows:
            rid, title, polymer, wvtr, units, temp, rh = row
            print(f"  [{rid}] {polymer}: {wvtr} {units}")
            print(f"      Artículo: {title[:60]}...")
            print(f"      Cond: {temp or '—'}, {rh or '—'}")
            print()

    cur.close()
    conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Explorar base de datos WVTR")
    parser.add_argument("--export", action="store_true", help="Exportar datos a CSV")
    parser.add_argument("--search", type=str, help="Buscar polímero por nombre")
    parser.add_argument("--all", action="store_true", help="Mostrar toda la información")

    args = parser.parse_args()

    if args.search:
        search_polymer(args.search)
    elif args.export:
        export_csv()
    else:
        explore()

    print("\n" + "=" * 70)
    print("USO:")
    python_cmd = "python" if sys.platform == "win32" else "python3"
    print(f"  {python_cmd} benchmark/explore_db.py           # Ver estadísticas")
    print(f"  {python_cmd} benchmark/explore_db.py --export  # Exportar a CSV")
    print(f"  {python_cmd} benchmark/explore_db.py --search PBAT  # Buscar polímero")
    print("=" * 70)
