"""Audit and consolidate WVTR rows against the current R2/result inventory."""

import argparse
import json
import unicodedata
from collections import Counter
from pathlib import Path

import psycopg2
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "temp" / "audit"


def norm(value):
    if value is None:
        return None
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(value.casefold().replace("‐", "-").split())


def connect():
    config = dotenv_values(ROOT / "API_KEY.env")
    return psycopg2.connect(host=config["PG_HOST"], port=config["PG_PORT"],
                            user=config["PG_USER"], password=config["PG_PASSWORD"],
                            dbname=config["PG_DATABASE"])


def current_inventory():
    records = []
    for path in sorted((ROOT / "temp" / "results").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = data.get("_meta", {})
        if meta.get("extractor_version") == "v3-vision":
            records.append({"pdf_key": meta.get("pdf_key"),
                            "doi": meta.get("doi"),
                            "article_title": data.get("article_title")})
    return [r for r in records if r["pdf_key"]]


def measurement_key(row):
    return tuple(norm(row[index]) for index in (2, 3, 4, 5, 6, 7, 8, 9, 10))


def audit(apply=False):
    inventory = current_inventory()
    keys = {item["pdf_key"] for item in inventory}
    by_doi = {item["doi"]: item["pdf_key"] for item in inventory if item["doi"]}
    by_title = {norm(item["article_title"]): item["pdf_key"] for item in inventory
                if item["article_title"]}
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, pdf_url, article_title, doi, polymer, wvtr_value,
                       wvtr_units, temperature, rh, thickness, test_method
                FROM wvtr_data ORDER BY id
            """)
            rows = cur.fetchall()
        current = [row for row in rows if row[1] in keys]
        historical = [row for row in rows if row[1] not in keys]
        current_keys = {measurement_key(row) for row in current}
        duplicate_ids = [row[0] for row in historical if measurement_key(row) in current_keys]
        reassigned = {}
        for row in historical:
            target = by_doi.get(row[3]) or by_title.get(norm(row[2]))
            if target and row[0] not in duplicate_ids:
                reassigned[row[0]] = target
        report = {
            "rows_before": len(rows), "current_rows": len(current),
            "historical_rows": len(historical), "exact_duplicate_ids": duplicate_ids,
            "exact_duplicates": len(duplicate_ids),
            "historical_unique": len(historical) - len(duplicate_ids),
            "reassigned_by_document": Counter(reassigned.values()),
            "unmapped_historical_ids": [row[0] for row in historical
                                         if row[0] not in duplicate_ids and row[0] not in reassigned],
        }
        report["reassigned_by_document"] = dict(report["reassigned_by_document"])
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "database_audit.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        if apply:
            with conn:
                with conn.cursor() as cur:
                    if duplicate_ids:
                        cur.execute("DELETE FROM wvtr_data WHERE id = ANY(%s)",
                                    (duplicate_ids,))
                    for row_id, pdf_key in reassigned.items():
                        cur.execute("UPDATE wvtr_data SET pdf_url = %s WHERE id = %s",
                                    (pdf_key, row_id))
            report["deleted"] = len(duplicate_ids)
            report["reassigned"] = len(reassigned)
        return report
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Delete confirmed duplicates and reassign historical rows")
    args = parser.parse_args(argv)
    report = audit(apply=args.apply)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
