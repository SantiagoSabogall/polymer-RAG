"""Tests de la ruta de imágenes del pipeline WVTR.

Sin red y sin PDF: se construyen manifiestos sintéticos que reproducen los
casos reales medidos en ``temp/markdown/10.1007_s10924-018-1256-x`` (logos de
105x97 px, figuras de 1506x1025 px, la fórmula química monocroma de 945x505 px).

    .venv/bin/python -m unittest discover -s tests -v
"""

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from image_filter import FilterConfig, filter_manifest, heuristic_reason  # noqa: E402
from image_manifest import (  # noqa: E402
    ImageCandidate,
    ManifestError,
    build_manifest,
    iter_nodes,
    load_manifest,
    page_sizes,
    write_manifest,
)
from llm_vision import pick_images  # noqa: E402
from prompt_vision import GATE_SCHEMA, VISION_SCHEMA  # noqa: E402
from reconcile import _norm_polymer, _norm_text, _norm_value, identity, reconcile  # noqa: E402
from vision_gate import EXTRACTABLE_KINDS, GateResult, ImageVerdict  # noqa: E402


def cand(name, page=1, w_pt=360, h_pt=272, w_px=1500, h_px=1135, **kw):
    """Atajo para un ImageCandidate con la geometría medida."""
    page_w, page_h = 595.3, 790.9
    defaults = dict(
        stem="paper", name=name, page=page,
        width_pt=w_pt, height_pt=h_pt,
        area_ratio=round(w_pt * h_pt / (page_w * page_h), 5),
        pos_top_ratio=0.5,
        width_px=w_px, height_px=h_px, bytes=100_000,
        ink_ratio=0.1, unique_colors=4097,
        has_page_size=True, path=f"/tmp/{name}",
    )
    defaults.update(kw)
    return ImageCandidate(**defaults)


class TestImageManifestGeometry(unittest.TestCase):
    """Los umbrales existen porque el bbox del outline no es [x,y,w,h]."""

    def test_bbox_parsed_as_x0y0x1y1(self):
        outline = {"kids": [{"type": "image", "page number": 3,
                             "bounding box": [10.0, 20.0, 110.0, 220.0],
                             "source": "paper/imageFile7.png"}]}
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "paper.md"
            md.write_text("texto\n\n![](<paper/imageFile7.png>)\n\nFig. 7 caption\n", encoding="utf-8")
            out = Path(tmp) / "paper.json"
            out.write_text(json.dumps(outline), encoding="utf-8")
            man = build_manifest(md, sizes={3: (595.3, 790.9)})

        self.assertEqual(len(man), 1)
        self.assertEqual(man[0].width_pt, 100.0)
        self.assertEqual(man[0].height_pt, 200.0)
        self.assertEqual(man[0].name, "imageFile7.png")

    def test_area_ratio_uses_real_page_size(self):
        outline = {"kids": [{"type": "image", "page number": 1,
                             "bounding box": [0, 0, 297.65, 395.45],
                             "source": "paper/imageFile1.png"}]}
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "paper.md"
            md.write_text("![](<paper/imageFile1.png>)", encoding="utf-8")
            (Path(tmp) / "paper.json").write_text(json.dumps(outline), encoding="utf-8")
            man = build_manifest(md, sizes={1: (595.3, 790.9)})
        self.assertAlmostEqual(man[0].area_ratio, 0.25, places=3)

    def test_without_page_size_no_crash_and_flagged(self):
        outline = {"kids": [{"type": "image", "page number": 1,
                             "bounding box": [0, 0, 300, 300],
                             "source": "paper/imageFile1.png"}]}
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "paper.md"
            md.write_text("![](<paper/imageFile1.png>)", encoding="utf-8")
            (Path(tmp) / "paper.json").write_text(json.dumps(outline), encoding="utf-8")
            man = build_manifest(md, sizes={})
        self.assertFalse(man[0].has_page_size)
        self.assertEqual(man[0].area_ratio, 0.0)

    def test_pos_top_ratio_flips_pdf_origin(self):
        """El PDF user space crece hacia arriba; una imagen pegada al borde
        superior de la página tiene pos_top_ratio ~0."""
        outline = {"kids": [{"type": "image", "page number": 1,
                             "bounding box": [0, 700.0, 300, 780.0],
                             "source": "paper/imageFile1.png"}]}
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "paper.md"
            md.write_text("![](<paper/imageFile1.png>)", encoding="utf-8")
            (Path(tmp) / "paper.json").write_text(json.dumps(outline), encoding="utf-8")
            man = build_manifest(md, sizes={1: (595.3, 790.9)})
        self.assertAlmostEqual(man[0].pos_top_ratio, 0.0138, places=3)

    def test_context_window_excludes_the_image_line(self):
        outline = {"kids": [{"type": "image", "page number": 1,
                             "bounding box": [0, 0, 300, 300],
                             "source": "paper/imageFile1.png"}]}
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "paper.md"
            md.write_text(
                "## Results\n\n![](<paper/imageFile1.png>)\n\n"
                "Table 3 shows WVTR for PBAT blends.\n", encoding="utf-8")
            (Path(tmp) / "paper.json").write_text(json.dumps(outline), encoding="utf-8")
            man = build_manifest(md)
        self.assertIn("Table 3 shows WVTR", man[0].context)
        self.assertNotIn("imageFile1.png", man[0].context)

    def test_missing_outline_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "paper.md"
            md.write_text("x", encoding="utf-8")
            self.assertEqual(build_manifest(md), [])

    def test_corrupt_outline_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "paper.md"
            md.write_text("x", encoding="utf-8")
            (Path(tmp) / "paper.json").write_text("{roto", encoding="utf-8")
            with self.assertRaises(ManifestError):
                build_manifest(md)

    def test_iter_nodes_does_not_recurse_into_scalars(self):
        payload = {"a": [1, "x", None, {"type": "image", "bounding box": [0, 0, 1, 1]}]}
        self.assertEqual(len(list(iter_nodes(payload))), 1)

    def test_manifest_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "paper.md"
            md.write_text("x", encoding="utf-8")
            original = [cand("imageFile1.png"), cand("imageFile2.png", page=2)]
            write_manifest(md, original)
            loaded = load_manifest(md)
        self.assertEqual([c.name for c in loaded], ["imageFile1.png", "imageFile2.png"])
        self.assertEqual(loaded[0].area_ratio, original[0].area_ratio)

    def test_load_manifest_missing_is_empty_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_manifest(Path(tmp) / "nada.md"), [])

    def test_page_sizes_on_non_pdf_is_empty(self):
        import logging
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "x.pdf"
            fake.write_bytes(b"not a pdf")
            # pypdf avisa por stderr al no encontrar el header; es esperado.
            with unittest.mock.patch.object(logging.getLogger("pypdf"), "warning"):
                self.assertEqual(page_sizes(fake), {})


class TestImageFilterRecalibrated(unittest.TestCase):
    """Los umbrales vienen de un paper real, no de suposiciones."""

    def test_publisher_logo_discarded_by_pixels(self):
        self.assertTrue(heuristic_reason(cand("logo.png", w_pt=25, h_pt=23, w_px=105, h_px=97))
                        .startswith("tiny_px"))

    def test_3x3_placeholder_discarded(self):
        self.assertTrue(heuristic_reason(cand("dot.png", w_pt=0.8, h_pt=0.6, w_px=3, h_px=3))
                        .startswith("tiny_px"))

    def test_real_figure_kept(self):
        self.assertEqual(heuristic_reason(cand("fig3.png", w_pt=361, h_pt=244, w_px=1506, h_px=1015)), "")

    def test_wide_strip_kept_to_preserve_recall(self):
        """imageFile26 real: 312x47 pt, 1299x197 px. Con "o" en vez de "y" en el
        test de puntos se habría descartado siendo una tabla válida."""
        self.assertEqual(heuristic_reason(cand("strip.png", w_pt=311.8, h_pt=47.3, w_px=1299, h_px=197)), "")

    def test_monochrome_chemistry_scheme_discarded(self):
        """imageFile25 real: estructura química del PLA, 2 colores, 1.5 % de página."""
        reason = heuristic_reason(cand("chem.png", w_pt=113.4, h_pt=60.6, w_px=945, h_px=505,
                                       unique_colors=2, area_ratio=0.0146))
        self.assertEqual(reason, "few_colors(2)")

    def test_header_logo_discarded_by_band(self):
        reason = heuristic_reason(cand("hdr.png", w_pt=80, h_pt=40, w_px=400, h_px=300,
                                       area_ratio=0.02, pos_top_ratio=0.02))
        self.assertTrue(reason.startswith("band"))

    def test_full_width_figure_touching_top_kept(self):
        """Una figura de ancho completo puede empezar al 7 % del alto: la banda
        de header solo descarta si además el área es pequeña."""
        self.assertEqual(heuristic_reason(cand("fig.png", w_pt=488, h_pt=160, w_px=2032, h_px=669,
                                              pos_top_ratio=0.07, area_ratio=0.166)), "")

    def test_filter_split_counts_reasons(self):
        manifest = [
            cand("logo.png", w_pt=25, h_pt=23, w_px=105, h_px=97),
            cand("fig1.png", w_pt=361, h_pt=244, w_px=1506, h_px=1015),
            cand("fig2.png", w_pt=361, h_pt=246, w_px=1506, h_px=1025),
        ]
        kept, reasons = filter_manifest(manifest)
        self.assertEqual(len(kept), 2)
        self.assertEqual(reasons, {"tiny_px": 1})

    def test_config_is_overridable(self):
        strict = FilterConfig(min_width_px=10, min_height_px=10, min_width_pt=1,
                              min_height_pt=1, logo_max_colors=0)
        self.assertEqual(heuristic_reason(cand("logo.png", w_pt=25, h_pt=23,
                                               w_px=105, h_px=97), strict), "")

    def test_thresholds_on_empty_manifest(self):
        self.assertEqual(filter_manifest([]), ([], {}))


class TestGateDecision(unittest.TestCase):
    """should_extract es la política: WVTR impreso + legible + tabla."""

    @staticmethod
    def result(kind, contains, readable, conf=0.9):
        return GateResult(
            candidate=cand("x.png"),
            verdict=ImageVerdict(kind=kind, contains_wvtr=contains,
                                 table_readable=readable,
                                 mentions_wvtr_terms=contains, confidence=conf,
                                 evidence="e"),
        )

    def test_table_with_wvtr_is_extracted(self):
        self.assertTrue(self.result("data_table", True, True).should_extract)

    def test_table_wvtr_unreadable_is_skipped(self):
        self.assertFalse(self.result("data_table", True, False).should_extract)

    def test_plot_with_wvtr_is_not_digitized(self):
        """Decisión de proyecto: los plots solo corroboran, no se leen."""
        self.assertFalse(self.result("plot", True, True).should_extract)

    def test_table_without_wvtr_is_skipped(self):
        self.assertFalse(self.result("data_table", False, True).should_extract)

    def test_only_data_table_is_extractable(self):
        self.assertEqual(EXTRACTABLE_KINDS, {"data_table"})

    def test_verdict_rejects_out_of_range_confidence(self):
        with self.assertRaises(Exception):
            ImageVerdict(kind="plot", contains_wvtr=False, table_readable=False,
                         mentions_wvtr_terms=False, confidence=1.7, evidence="")

    def test_verdict_rejects_unknown_kind(self):
        with self.assertRaises(Exception):
            ImageVerdict(kind="inventado", contains_wvtr=False, table_readable=False,
                         mentions_wvtr_terms=False, confidence=0.5, evidence="")


class TestSchemas(unittest.TestCase):
    """Structured outputs exige un subconjunto estricto de JSON Schema."""

    def test_gate_schema_is_strict_compatible(self):
        schema = GATE_SCHEMA["schema"]
        self.assertTrue(GATE_SCHEMA["strict"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(sorted(schema["required"]), sorted(schema["properties"]))

    def test_vision_schema_records_are_strict(self):
        item = VISION_SCHEMA["schema"]["properties"]["registros"]["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(sorted(item["required"]), sorted(item["properties"]))

    def test_vision_schema_has_provenance_fields(self):
        item = VISION_SCHEMA["schema"]["properties"]["registros"]["items"]
        for field in ("source_ref", "source_page"):
            self.assertIn(field, item["properties"])


class TestValueNormalization(unittest.TestCase):
    def test_decimal_comma(self):
        self.assertEqual(_norm_value("4,5"), 4.5)

    def test_leading_zero_is_decimal_not_thousands(self):
        self.assertEqual(_norm_value("0.045"), 0.045)

    def test_thousands_separator(self):
        self.assertEqual(_norm_value("1,234"), 1234.0)

    def test_scientific_notation(self):
        self.assertEqual(_norm_value("4.5e-3"), 0.0045)

    def test_units_suffix_is_stripped(self):
        self.assertEqual(_norm_value("4.5 g/m2/day"), 4.5)

    def test_unit_string_is_not_a_value(self):
        self.assertIsNone(_norm_value("g/m2"))

    def test_tolerance_symbols(self):
        self.assertEqual(_norm_value("~4.2"), 4.2)
        self.assertEqual(_norm_value(">1000"), 1000.0)

    def test_unicode_minus(self):
        self.assertEqual(_norm_value("\u22122.5"), -2.5)

    def test_float_noise_rounds_to_four_significant_digits(self):
        self.assertEqual(_norm_value(4.499999), 4.5)

    def test_polymer_and_units_normalization(self):
        self.assertEqual(_norm_polymer("PBAT-50"), _norm_polymer("PBAT50"))
        self.assertEqual(_norm_text("g/m\u00b2/day"), _norm_text("G/M2/DAY"))


class TestReconcile(unittest.TestCase):
    TEXT_ROWS = [
        {"polymer": "PBAT", "wvtr_value": 4.5, "wvtr_units": "g/m2/day",
         "temperature": "25C", "rh": "50%", "thickness": None, "test_method": "ASTM F1249"},
    ]
    IMAGE_ROWS = [
        {"polymer": "PBAT", "wvtr_value": "4,5", "wvtr_units": "G/M2/DAY",
         "temperature": None, "rh": "50%", "thickness": "40 um",
         "test_method": None, "source_ref": "imageFile9.png", "source_page": 12},
    ]

    def test_same_measurement_from_both_routes_collapses(self):
        merged, stats = reconcile(self.TEXT_ROWS, self.IMAGE_ROWS)
        self.assertEqual(len(merged), 1)
        row = merged[0]
        self.assertEqual(row["source_kind"], "text+image")
        self.assertTrue(row["corroborated"])
        # Condiciones ausentes en texto se rellenan desde la imagen.
        self.assertEqual(row["thickness"], "40 um")
        # Y las del texto no se pisan.
        self.assertEqual(row["temperature"], "25C")
        self.assertEqual(stats.corroborated, 1)

    def test_image_only_row_enters_with_provenance(self):
        merged, stats = reconcile([], [
            {"polymer": "PHB", "wvtr_value": 12.0, "wvtr_units": "g/m2/day",
             "source_ref": "imageFile3.png", "source_page": 7}])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_kind"], "image")
        self.assertEqual(merged[0]["source_ref"], "imageFile3.png")
        self.assertEqual(stats.corroborated, 0)

    def test_text_only_row_is_untouched(self):
        merged, _ = reconcile(self.TEXT_ROWS, [])
        self.assertEqual(merged[0]["source_kind"], "text")
        self.assertFalse(merged[0]["corroborated"])

    def test_duplicate_inside_text_is_collapsed_keeping_richest(self):
        rows = [
            {"polymer": "PBAT", "wvtr_value": 4.5, "wvtr_units": "g/m2/day"},
            {"polymer": "PBAT", "wvtr_value": "4.50", "wvtr_units": "g/m2/day",
             "rh": "90%", "thickness": "25 um"},
        ]
        merged, stats = reconcile(rows, [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["rh"], "90%")
        self.assertEqual(stats.duplicates_dropped, 1)

    def test_same_value_different_units_is_two_rows(self):
        """Un valor en g/m2/day y otro en g/mm2/day son mediciones distintas."""
        merged, _ = reconcile(
            [{"polymer": "PBAT", "wvtr_value": 4.5, "wvtr_units": "g/m2/day"}],
            [{"polymer": "PBAT", "wvtr_value": 4.5, "wvtr_units": "g/mm2/day"}])
        self.assertEqual(len(merged), 2)

    def test_different_sample_code_stays_a_separate_row(self):
        """'PBAT' y 'PBAT50' son formulaciones distintas: fusionarlas perdería
        un dato. El código de muestra es parte de la identidad."""
        merged, _ = reconcile(
            [{"polymer": "PBAT", "wvtr_value": 4.5, "wvtr_units": "g/m2/day"}],
            [{"polymer": "PBAT50", "wvtr_value": 4.5, "wvtr_units": "g/m2/day",
              "source_ref": "imageFile9.png", "source_page": 12}])
        self.assertEqual(len(merged), 2)

    def test_same_value_different_polymer_is_two_rows(self):
        merged, _ = reconcile(
            [{"polymer": "PBAT", "wvtr_value": 4.5, "wvtr_units": "g/m2/day"}],
            [{"polymer": "PLA", "wvtr_value": 4.5, "wvtr_units": "g/m2/day"}])
        self.assertEqual(len(merged), 2)

    def test_rows_without_value_are_dropped(self):
        merged, stats = reconcile(
            [{"polymer": "PBAT", "wvtr_value": None, "wvtr_units": "g/m2/day"}], [])
        self.assertEqual(merged, [])
        self.assertEqual(stats.duplicates_dropped, 1)

    def test_identity_ignores_conditions_by_design(self):
        a = {"polymer": "PBAT", "wvtr_value": 4.5, "wvtr_units": "g/m2/day",
             "temperature": "25C", "rh": "50%"}
        b = {"polymer": "PBAT", "wvtr_value": 4.5, "wvtr_units": "g/m2/day",
             "temperature": "40C", "rh": "90%"}
        self.assertEqual(identity(a), identity(b))

    def test_empty_inputs(self):
        merged, stats = reconcile([], [])
        self.assertEqual(merged, [])
        self.assertEqual(stats.text_records, 0)

    def test_order_is_stable_text_first(self):
        merged, _ = reconcile(
            [{"polymer": "A", "wvtr_value": 1, "wvtr_units": "u"},
             {"polymer": "B", "wvtr_value": 2, "wvtr_units": "u"}],
            [{"polymer": "C", "wvtr_value": 3, "wvtr_units": "u"}])
        self.assertEqual([r["polymer"] for r in merged], ["A", "B", "C"])


class TestVisionSelection(unittest.TestCase):
    def test_picks_largest_first_and_respects_cap(self):
        manifest = [cand(f"imageFile{i}.png", area_ratio=0.01 * i, w_px=300 + i, h_px=300)
                    for i in range(1, 11)]
        chosen = pick_images(manifest, limit=3)
        self.assertEqual(len(chosen), 3)
        self.assertEqual([c.area_ratio for c in chosen],
                         [0.1, 0.09, 0.08])

    def test_fewer_than_cap(self):
        self.assertEqual(len(pick_images([cand("a.png")], limit=6)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
