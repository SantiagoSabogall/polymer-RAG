import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from pydantic import ValidationError
from src import analytics


def plan(**changes):
    values = dict(operation="count", field=None, filters=[], order="desc", limit=10, reason="")
    values.update(changes)
    return analytics.AnalyticsPlan(**values)


class AnalyticsTests(unittest.TestCase):
    def test_recognizes_user_questions(self):
        for question in ("cuantos de los 84 datos tienen temperatura",
                         "que articulo es el que mas aporta valores de wvtr",
                         "cuántos artículos hay", "promedio de WVTR"):
            self.assertTrue(analytics.is_analytics_question(question))
        self.assertFalse(analytics.is_analytics_question("Explica el WVTR del PLA"))

    def test_validated_columns_and_operations(self):
        with self.assertRaises(ValidationError):
            plan(field="polymer; DROP TABLE wvtr_data")
        with self.assertRaises(ValidationError):
            plan(operation="delete")
        with self.assertRaises(ValidationError):
            plan(limit=100000)

    def test_filter_value_is_bound_parameter(self):
        value = "PLA' OR 1=1 --"
        _, params = analytics.compile_query(plan(filters=[
            dict(field="polymer", operator="equals", value=value)]))
        self.assertEqual(params, [value])

    def test_celsius_rejects_other_columns(self):
        with self.assertRaises(ValueError):
            analytics.compile_query(plan(filters=[
                dict(field="polymer", operator="celsius", value="38")]))

    def test_returns_exact_count_without_embedding_or_answer_generation(self):
        client = MagicMock()
        request_plan = plan(filters=[dict(field="temperature", operator="present", value="")])
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=request_plan.model_dump_json()))])
        with patch.object(analytics, "execute_analytics", return_value=[(67,)]) as execute:
            result = analytics.analytics_answer(client, "model", "cuántos tienen temperatura")
        execute.assert_called_once()
        self.assertIn("**67 registros**", result["answer"])
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertEqual(result["sources"], [])

    def test_unsupported_question_never_queries_database(self):
        with patch.object(analytics, "plan_analytics",
                          return_value=plan(operation="unsupported", reason="Especifica la unidad.")),                 patch.object(analytics, "execute_analytics") as execute:
            result = analytics.analytics_answer(None, "", "promedio")
        execute.assert_not_called()
        self.assertEqual(result["answer"], "Especifica la unidad.")

    def test_ranking_preserves_tied_results_in_answer(self):
        answer = analytics.format_analytics(
            plan(operation="ranking", field="article_title", limit=1),
            [("A", 16), ("B", 16)])
        self.assertIn("A — **16 registros**", answer)
        self.assertIn("B — **16 registros**", answer)

    def test_presence_filter_accepts_null_value_from_provider(self):
        parsed = plan(filters=[dict(field="temperature", operator="present", value=None)])
        self.assertEqual(parsed.filters[0].value, "")
        _, params = analytics.compile_query(parsed)
        self.assertEqual(params, [analytics.MISSING])

    def test_invalid_provider_plan_requests_clarification(self):
        with patch.object(analytics, "plan_analytics", side_effect=ValueError("bad plan")),                 patch.object(analytics, "execute_analytics") as execute:
            result = analytics.analytics_answer(None, "", "cuántos")
        execute.assert_not_called()
        self.assertIn("No pude interpretar", result["answer"])

    def test_rag_routes_statistics_before_semantic_search(self):
        import os
        with patch.dict(os.environ, {"OPENROUTE_API": "test"}):
            import sys
            sys.modules.pop("src.config", None)
            from src import rag
        expected = {"answer": "67", "sources": [], "context_used": "", "db_stats": {}}
        with patch.object(rag, "analytics_answer", return_value=expected) as analyze,                 patch.object(rag, "search_similar") as search,                 patch.object(rag, "generate_answer") as generate:
            self.assertEqual(rag.rag_query("cuántos tienen temperatura"), expected)
        analyze.assert_called_once()
        search.assert_not_called()
        generate.assert_not_called()

    def test_numeric_temperature_from_provider_is_bound_safely(self):
        parsed = plan(filters=[dict(field="temperature", operator="celsius", value=38)])
        self.assertEqual(parsed.filters[0].value, "38")
        _, params = analytics.compile_query(parsed)
        self.assertEqual(params[-1], "38")
