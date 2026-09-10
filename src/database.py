import json
import logging
import psycopg2
from psycopg2.extras import execute_values
from config import PG_HOST, PG_DATABASE, PG_USER, PG_PASSWORD

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    pass


def get_connection():
    try:
        conn = psycopg2.connect(
            host=PG_HOST,
            database=PG_DATABASE,
            user=PG_USER,
            password=PG_PASSWORD
        )
        return conn
    except Exception as e:
        raise DatabaseError(f"Error conectando a PostgreSQL: {e}")


def insert_wvtr(conn, article_title, doi, pdf_url, llm_result, raw_json):
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
            execute_values(cur, query, rows)
        conn.commit()
        logger.info(f"Insertados {len(rows)} registros WVTR")
        return len(rows)
    except Exception as e:
        conn.rollback()
        raise DatabaseError(f"Error insertando datos: {e}")


def insert_batch(conn, results, dois):
    total_inserted = 0

    for result in results:
        pdf_key = result.get("pdf_key")
        doi = result.get("doi")
        llm_result = {
            "article_title": result.get("article_title"),
            "registros": result.get("registros", [])
        }
        raw_json = result
        pdf_url = dois.get(pdf_key, "") or pdf_key

        try:
            n = insert_wvtr(
                conn,
                article_title=result.get("article_title"),
                doi=doi,
                pdf_url=pdf_url,
                llm_result=llm_result,
                raw_json=raw_json
            )
            total_inserted += n
        except DatabaseError as e:
            logger.error(f"Error insertando {pdf_key}: {e}")
            continue

    return total_inserted


def get_stats(conn):
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
