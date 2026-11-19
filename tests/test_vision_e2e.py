"""Test end-to-end de la ruta de imágenes (requiere OPENROUTE_API en API_KEY.env).

Se salta solo si no hay credenciales, así que ``unittest discover`` sigue
siendo verde sin red. Es un test de integración de verdad: descarga nada, pero
llama al gate y al extractor sobre una tabla rasterizada sintética con verdad
terreno conocido, y verifica los valores uno a uno.

    .venv/bin/python -m unittest tests.test_vision_e2e -v
"""

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

try:
    from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
    HAS_KEY = bool(OPENROUTER_API_KEY)
except Exception:
    HAS_KEY = False

from make_wvtr_table import EXPECTED, build  # noqa: E402

from image_filter import filter_manifest  # noqa: E402
from image_manifest import ImageCandidate  # noqa: E402
from llm_vision import extract_all  # noqa: E402
from reconcile import reconcile  # noqa: E402


def make_candidate(path: Path) -> ImageCandidate:
    """Geometría realista: la tabla ocupa ~1/2 del ancho de una página A4."""
    from PIL import Image

    with Image.open(path) as im:
        width_px, height_px = im.size
    width_pt, height_pt = 300.0, 110.0
    return ImageCandidate(
        stem="synthetic", name=path.name, page=7,
        width_pt=width_pt, height_pt=height_pt,
        area_ratio=round(width_pt * height_pt / (595.3 * 790.9), 5),
        pos_top_ratio=0.35, width_px=width_px, height_px=height_px,
        bytes=path.stat().st_size, ink_ratio=0.09, unique_colors=4097,
        has_page_size=True, path=str(path),
        context="Table 2. Water vapor transmission rate of PBAT based films.",
    )


@unittest.skipUnless(HAS_KEY, "sin OPENROUTE_API en API_KEY.env")
class TestVisionEndToEnd(unittest.TestCase):
    table_path: Path

    @classmethod
    def setUpClass(cls):
        cls.table_path = Path(build(ROOT / "tests" / "fixtures" / "wvtr_table_raster.png"))
        cls.candidate = make_candidate(cls.table_path)

    def test_01_heuristic_keeps_a_real_wvtr_table(self):
        kept, drops = filter_manifest([self.candidate])
        self.assertEqual(len(kept), 1, f"el filtro descartó la tabla WVTR válida: {drops}")

    def test_02_gate_selects_it_and_extractor_matches_ground_truth(self):
        from openai import AsyncOpenAI

        from vision_gate import gate_all

        client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)

        gates, failures, _ = asyncio.run(gate_all(client, [self.candidate], doi="10.1234/syn"))
        self.assertEqual(len(failures), 0)
        verdict = gates[0].verdict
        self.assertTrue(verdict.contains_wvtr, f"gate: {verdict.evidence}")
        self.assertTrue(verdict.table_readable, f"gate: {verdict.evidence}")
        self.assertEqual(verdict.kind, "data_table")
        self.assertTrue(gates[0].should_extract)

        extractions, stats = asyncio.run(
            extract_all(client, [gates[0].candidate], doi="10.1234/syn")
        )
        self.assertEqual(stats["vision_errors"], 0)
        records = extractions[0].records
        self.assertEqual(len(records), len(EXPECTED),
                         f"esperaba {len(EXPECTED)} registros, obtuve {len(records)}: {records}")

        expected_by_polymer = {row[0]: row for row in EXPECTED}
        for record in records:
            polymer = record["polymer"]
            self.assertIn(polymer, expected_by_polymer,
                          f"polímero inventado: {polymer!r}")
            _, value, units, temp, rh, thickness, method = expected_by_polymer[polymer]
            self.assertAlmostEqual(float(record["wvtr_value"]), float(value), places=2)
            self.assertEqual(record["wvtr_units"], units)
            self.assertEqual(record["temperature"], temp)
            self.assertEqual(record["rh"], rh)
            self.assertEqual(record["thickness"], thickness)
            self.assertEqual(record["test_method"], method)
            self.assertEqual(record["source_ref"], self.candidate.name)

    def test_03_reconcile_does_not_duplicate_overlapping_measurements(self):
        """3 de texto + 5 de imagen con 2 solapadas -> 6 filas, no 8."""
        from openai import AsyncOpenAI

        from llm_vision import extract_all

        client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)
        extractions, _ = asyncio.run(
            extract_all(client, [self.candidate], doi="10.1234/syn")
        )
        image_records = extractions[0].records
        text_records = [
            {"polymer": "PBAT", "wvtr_value": 1.2, "wvtr_units": "g/m2/day",
             "temperature": "25 C", "rh": "50%", "thickness": "40 um",
             "test_method": "ASTM F1249"},
            {"polymer": "PBAT + 1% MMT", "wvtr_value": 0.6, "wvtr_units": "g/m2/day",
             "temperature": "25 C", "rh": "50%", "thickness": "40 um",
             "test_method": "ASTM F1249"},
            {"polymer": "EVOH", "wvtr_value": 4.5, "wvtr_units": "g/m2/day",
             "temperature": "38 C", "rh": "90%", "thickness": "15 um",
             "test_method": "ASTM F1249"},
        ]

        merged, stats = reconcile(text_records, image_records)
        # 3 + 5 - 2 solapadas
        self.assertEqual(len(merged), len(text_records) + len(image_records) - 2)
        self.assertEqual(stats.corroborated, 2)
        self.assertEqual(stats.duplicates_dropped, 0)

        kinds = [r["source_kind"] for r in merged]
        self.assertEqual(kinds.count("text+image"), 2)
        self.assertEqual(kinds.count("text"), 1)
        self.assertEqual(kinds.count("image"), 3)

        # La fila corroborada conserva el valor del texto y su procedencia.
        corroborated = [r for r in merged if r["source_kind"] == "text+image"]
        for row in corroborated:
            self.assertTrue(row["corroborated"])
            self.assertIsNone(row.get("source_ref"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
