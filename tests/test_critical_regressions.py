import importlib.util
import json
from decimal import Decimal
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src import database
from src.reconcile import reconcile


class CriticalRegressions(unittest.TestCase):
    def test_conflicting_conditions_survive(self):
        base = dict(polymer="PLA", wvtr_value=4.2, wvtr_units="g/m2/day")
        rows, _ = reconcile([dict(base, temperature="23C"),
                             dict(base, temperature="38C")], [base])
        self.assertEqual(len(rows), 3)
        self.assertFalse(any(r["corroborated"] for r in rows))

    def test_image_duplicates_are_not_text_evidence(self):
        row = dict(polymer="PLA", wvtr_value=4.2, wvtr_units="g/m2/day")
        rows, _ = reconcile([], [row, row])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_kind"], "image")
        self.assertFalse(rows[0]["corroborated"])

    def test_unchanged_database_rows_preserve_ids_and_vectors(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        record = dict(polymer="PLA", wvtr_value=4.2)
        cur.fetchall.return_value = [
            ("title", None, "PLA", Decimal("4.2"), None, None, None, None, None,
             "paper.pdf", {}, None, None, None, None, None)]
        with patch.object(database, "execute_values") as insert:
            count = database.replace_document(conn, "title", None, "paper.pdf",
                                              {"registros": [record]}, {})
        self.assertEqual(count, 0)
        insert.assert_not_called()
        self.assertFalse(any("DELETE" in c.args[0] for c in cur.execute.call_args_list))

    def test_valid_empty_result_removes_old_measurements(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [
            ("title", None, "PLA", Decimal("4.2"), None, None, None, None, None,
             "paper.pdf", {}, None, None, None, None, None)]
        with patch.object(database, "execute_values") as insert:
            database.replace_document(conn, "title", None, "paper.pdf",
                                      {"registros": []}, {})
        insert.assert_not_called()
        self.assertTrue(any("DELETE" in c.args[0] for c in cur.execute.call_args_list))
        conn.commit.assert_called_once()

    def test_embeddings_use_measurement_columns_and_zero(self):
        with patch.dict(os.environ, {"OPENROUTE_API": "test"}):
            import sys
            sys.modules.pop("config", None)
            from scripts import generate_embeddings as script
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchall.return_value = [
            (1, "Paper", "PLA", 0, "g/m2/day", "23C", "50%", "10um", "ASTM")]
        with patch.object(script, "get_connection", return_value=conn),                 patch.object(script, "release_connection"),                 patch.object(script, "get_embedding", return_value=[1]) as embed:
            script.generate_all_embeddings()
        text = embed.call_args.args[0]
        self.assertIn("Polymer: PLA", text)
        self.assertIn("WVTR: 0", text)
        self.assertIn("Temperature: 23C", text)

    def test_legacy_cache_is_reprocessed_and_current_cache_skipped(self):
        import asyncio
        from unittest.mock import AsyncMock
        spec = importlib.util.spec_from_file_location(
            "src.vision_cli", Path(__file__).resolve().parents[1] / "src" / "pipeline.py")
        module = importlib.util.module_from_spec(spec)
        # Importing the existing CLI configures a file logger.
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                spec.loader.exec_module(module)
            finally:
                os.chdir(previous)
            root = Path(tmp)
            md = root / "paper.md"
            md.write_text("paper")
            module.TEMP_RESULTS = root
            module.TEMP_MARKDOWN_CLEANED = root
            state = {"paper.pdf": {"status": "completed", "etag": "1"}}
            output = module._result_path_for_md(md)
            for version, calls in [("legacy", 1), (module.EXTRACTOR_VERSION, 0)]:
                output.write_text(json.dumps({
                    "registros": [], "_meta": {"etag": "1", "extractor_version": version}}))
                with patch.object(module, "AsyncOpenAI"),                         patch.object(module, "process_file", new_callable=AsyncMock,
                                     return_value={"registros": []}) as process:
                    asyncio.run(module.run_phase_2({}, state))
                self.assertEqual(process.await_count, calls)

    def test_image_gate_verdict_serializes_and_text_result_is_saved(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        spec = importlib.util.spec_from_file_location(
            "src.vision_cli_regression", Path(__file__).resolve().parents[1] / "src" / "pipeline.py")
        module = importlib.util.module_from_spec(spec)
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                spec.loader.exec_module(module)
            finally:
                os.chdir(previous)
            from vision_gate import GateResult, ImageVerdict
            from image_manifest import ImageCandidate
            candidate = ImageCandidate(stem="paper", name="figure.png")
            verdict = ImageVerdict(kind="data_table", contains_wvtr=False,
                                   table_readable=True, mentions_wvtr_terms=False,
                                   confidence=0.9)
            gate = GateResult(candidate, verdict, cached=True)
            self.assertEqual(gate.to_dict()["model_verdict"]["confidence"], 0.9)
            root = Path(tmp)
            module.TEMP_RESULTS = root
            md = root / "paper.md"
            md.write_text("paper")
            row = {"polymer": "PLA", "wvtr_value": 4.2, "wvtr_units": "g/m2/day"}
            response = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content=json.dumps({"registros": [row]})))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))
            client = MagicMock()
            client.chat.completions.create = AsyncMock(return_value=response)
            module.llm_stats["start_time"] = module.time.time()
            with patch.object(module, "load_manifest", return_value=[candidate]),                     patch.object(module, "filter_manifest", return_value=([candidate], {})),                     patch.object(module, "gate_all", new_callable=AsyncMock,
                                 return_value=([gate], [], {
                                     "gate_tokens_in": 0, "gate_tokens_out": 0,
                                     "gate_to_extract": 0})):
                result = asyncio.run(module.process_file(
                    client, asyncio.Semaphore(1), md, "", "paper.pdf", "etag", 1))
            saved = json.loads(module._result_path_for_md(md).read_text())
            self.assertEqual(saved, result)
            self.assertEqual(len(saved["registros"]), 1)
            self.assertEqual(module.llm_stats["total_records"], 1)
