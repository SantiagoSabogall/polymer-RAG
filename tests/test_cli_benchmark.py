import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from benchmark import evaluate, run_model
from src import pipeline


def entry(rows):
    return {"success": True, "result": {"article_title": "Paper", "registros": rows}}


ROW = {"polymer": "PLA", "wvtr_value": 1, "wvtr_units": "g/m2/day"}


class BenchmarkTests(unittest.TestCase):
    def test_wrong_polymer_and_value_cannot_match_missing_conditions(self):
        score = evaluate.evaluate_paper(entry([dict(ROW, polymer="PBAT", wvtr_value=100)]), [ROW])
        self.assertEqual(score["recall"], 0)
        self.assertEqual(score["precision"], 0)

    def test_tolerance_is_relative_to_ground_truth(self):
        self.assertFalse(evaluate.same_measurement(dict(ROW, wvtr_value=1.11), ROW))
        self.assertTrue(evaluate.same_measurement(dict(ROW, wvtr_value=1.09), ROW))

    def test_duplicate_prediction_is_false_positive(self):
        score = evaluate.evaluate_paper(entry([ROW, ROW]), [ROW])
        self.assertEqual(score["recall"], 1)
        self.assertEqual(score["precision"], 0.5)

    def test_units_and_nonfinite_values_cannot_match(self):
        for row in (dict(ROW, wvtr_units="g/mm/day"), dict(ROW, wvtr_value=float("nan"))):
            self.assertEqual(evaluate.evaluate_paper(entry([row]), [ROW])["recall"], 0)

    def test_unannotated_papers_are_excluded(self):
        result = evaluate.evaluate_results({"model": [
            {"paper": "unknown.md", **entry([ROW])},
            {"paper": "empty.md", **entry([])}]}, {"empty.md": {"expected": []}})
        self.assertEqual(result["model"]["evaluated_papers"], 1)
        self.assertEqual(result["model"]["unannotated_papers"], ["unknown.md"])
        self.assertEqual(result["model"]["avg_precision"], 1)

    def test_no_annotations_produces_no_score(self):
        metrics = evaluate.evaluate_results({"model": [{"paper": "x", **entry([ROW])}]}, {})["model"]
        self.assertIsNone(metrics["avg_recall"])

    def test_runner_exports_evaluable_results_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, outputs = root / "inputs", root / "outputs"
            inputs.mkdir()
            paper = inputs / "paper.md"
            paper.write_text("PLA measurements")
            response = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(entry([ROW])["result"])))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))
            client = MagicMock()
            client.chat.completions.create = AsyncMock(return_value=response)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=None)
            with patch.object(run_model, "AsyncOpenAI", return_value=client):
                for _ in range(2):
                    result = asyncio.run(run_model.run_benchmark_async("test/model", inputs, outputs))
                self.assertEqual(client.chat.completions.create.await_count, 1)
                paper.write_text("Changed measurements")
                asyncio.run(run_model.run_benchmark_async("test/model", inputs, outputs))
                self.assertEqual(client.chat.completions.create.await_count, 2)
            aggregate = json.loads(next(outputs.glob("results-*.json")).read_text())
            metrics = evaluate.evaluate_results(aggregate, {"paper.md": {"expected": [ROW]}})
            self.assertEqual(metrics["test/model"]["avg_recall"], 1)
            self.assertTrue(result[0]["success"])

    def test_cli_dispatches_once_without_recursion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text("data")
            with patch.object(run_model, "run_benchmark_async", new_callable=AsyncMock,
                              return_value=[{"success": True}]) as run:
                self.assertEqual(run_model.main(["test/model", "--input-dir", directory]), 0)
            run.assert_awaited_once()


class PipelineCliTests(unittest.TestCase):
    def test_legacy_wrappers_share_implementation(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("main.py", "main_test.py"):
            spec = importlib.util.spec_from_file_location("wrapper", root / filename)
            wrapper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(wrapper)
            self.assertIs(wrapper.main, pipeline.main)

    def test_help_has_no_filesystem_side_effects(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1", OPENROUTE_API="")
            result = subprocess.run([sys.executable, "-m", "src.pipeline", "--help"],
                                    cwd=directory, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--phase", result.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_phase_one_never_calls_extraction_or_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.multiple(pipeline, TEMP_DIR=root, PROGRESS_FILE=root/"progress.json",
                                ERRORS_FILE=root/"errors.json", DOIS_FILE=root/"dois.json",
                                TEMP_RESULTS=root, TEMP_MARKDOWN_CLEANED=root),                     patch.object(pipeline, "ensure_dirs"),                     patch.object(pipeline.logging, "basicConfig"),                     patch.object(pipeline, "get_s3_client"),                     patch.object(pipeline, "list_pdf_objects", return_value=[]),                     patch.object(pipeline, "run_phase_1", return_value=(0, [])),                     patch.object(pipeline, "run_phase_2") as extract,                     patch.object(pipeline, "run_phase_3") as publish:
                pipeline.main(["--phase", "1"])
            extract.assert_not_called()
            publish.assert_not_called()
