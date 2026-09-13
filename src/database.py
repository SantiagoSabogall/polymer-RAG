import json
import logging
import psycopg2
from psycopg2 import pool
from psycopg2.extras import execute_values
try:
    from .config import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD
except ImportError:
    from config import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    pass


# ============================================
# CONNECTION POOLING
# ============================================
connection_pool = None

def init_pool(minconn=2, maxconn=10):
    """Inicializa el pool de conexiones a PostgreSQL."""
    global connection_pool
    try:
        connection_pool = pool.ThreadedConnectionPool(
            minconn, maxconn,
            host=PG_HOST,
            port=PG_PORT,
            database=PG_DATABASE,
            user=PG_USER,
            password=PG_PASSWORD
        )
        logger.info(f"Pool de conexiones inicializado: {minconn}-{maxconn} conexiones")
    except Exception as e:
        raise DatabaseError(f"Error inicializando pool de conexiones: {e}")

def get_connection():
    """Obtiene una conexión del pool."""
    global connection_pool
    if connection_pool is None:
        init_pool()
    try:
        return connection_pool.getconn()
    except Exception as e:
        raise DatabaseError(f"Error obteniendo conexión del pool: {e}")

def release_connection(conn):
    """Libera una conexión de vuelta al pool."""
    global connection_pool
    if connection_pool and conn:
        connection_pool.putconn(conn)


def insert_wvtr(conn, article_title, doi, pdf_url, llm_result, raw_json):
    """Inserta registros WVTR para un artículo."""
    registros = llm_result.get("registros", [])

    if not registros:
        logger.info("No hay registros WVTR para insertar")
        return 0

    rows = []
    for reg in registros:
        rows.append((
            article_title,
            doi,
            reg.get("polymer"),
            reg.get("wvtr_value"),
            reg.get("wvtr_units"),
            reg.get("temperature"),
            reg.get("rh"),
            reg.get("thickness"),
            reg.get("test_method"),
            pdf_url,
            json.dumps(raw_json, ensure_ascii=False)
        ))

    query = """
        INSERT INTO wvtr_data (
            article_title, doi, polymer, wvtr_value, wvtr_units,
            temperature, rh, thickness, test_method, pdf_url, raw_json
        ) VALUES %s
    """

    try:
        with conn.cursor() as cur:
            execute_values(cur, query, rows, page_size=1000)
        conn.commit()
        logger.info(f"Insertados {len(rows)} registros WVTR")
        return len(rows)
    except Exception as e:
        conn.rollback()
        raise DatabaseError(f"Error insertando datos: {e}")


def insert_wvtr_batch(conn, records):
    """Inserta múltiples registros WVTR de una vez (batch insert)."""
    if not records:
        return 0

    query = """
        INSERT INTO wvtr_data (
            article_title, doi, polymer, wvtr_value, wvtr_units,
            temperature, rh, thickness, test_method, pdf_url, raw_json
        ) VALUES %s
    """

    try:
        with conn.cursor() as cur:
            execute_values(cur, query, records, page_size=1000)
        conn.commit()
        logger.info(f"Batch insert: {len(records)} registros WVTR")
        return len(records)
    except Exception as e:
        conn.rollback()
        raise DatabaseError(f"Error en batch insert: {e}")


def insert_batch(conn, results, dois):
    """Inserta resultados del LLM en batch."""
    total_inserted = 0
    all_rows = []

    for result in results:
        pdf_key = result.get("pdf_key")
        doi = result.get("doi")
        article_title = result.get("article_title")
        registros = result.get("registros", [])
        pdf_url = dois.get(pdf_key, "") or pdf_key

        for reg in registros:
            all_rows.append((
                article_title,
                doi,
                reg.get("polymer"),
                reg.get("wvtr_value"),
                reg.get("wvtr_units"),
                reg.get("temperature"),
                reg.get("rh"),
                reg.get("thickness"),
                reg.get("test_method"),
                pdf_url,
                json.dumps(result, ensure_ascii=False)
            ))

    if all_rows:
        total_inserted = insert_wvtr_batch(conn, all_rows)

    return total_inserted


def is_already_processed(doi):
    """Verifica si un DOI ya existe en la base de datos."""
    if not doi:
        return False

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM wvtr_data WHERE doi = %s LIMIT 1", (doi,))
        exists = cur.fetchone() is not None
        cur.close()
        return exists
    except Exception as e:
        logger.error(f"Error verificando DOI: {e}")
        return False
    finally:
        release_connection(conn)


def get_stats(conn):
    """Obtiene estadísticas de la base de datos."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM wvtr_data")
            total = cur.fetchone()[0]

            cur.execute("SELECT COUNT(DISTINCT doi) FROM wvtr_data WHERE doi IS NOT NULL")
            dois = cur.fetchone()[0]

            cur.execute("SELECT COUNT(DISTINCT polymer) FROM wvtr_data")
            polymers = cur.fetchone()[0]

            return {
                "total_registros": total,
                "dois_unicos": dois,
                "polimeros_unicos": polymers
            }
    except Exception as e:
        raise DatabaseError(f"Error consultando estadísticas: {e}")
