"""Evalúa predicciones contra artículos anotados, con cobertura explícita."""

import argparse
import json
import math
from pathlib import Path


def fuzzy_match(a, b):
    if a is None or b is None:
        return a == b
    return str(a).strip().casefold() == str(b).strip().casefold()


def normalize_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def same_measurement(record, expected):
    """Exige polímero, unidades y valor; tolerancia relativa de 10 % heredada."""
    if not record.get("polymer") or not record.get("wvtr_units"):
        return False
    if not all(fuzzy_match(record.get(field), expected.get(field))
               for field in ("polymer", "wvtr_units")):
        return False
    value, target = normalize_number(record.get("wvtr_value")), normalize_number(expected.get("wvtr_value"))
    return (value is not None and target is not None
            and abs(value - target) <= max(abs(target) * 0.1, 1e-9))


def matchRegistro(record, expected):
    fields = ("polymer", "wvtr_units", "temperature", "rh", "thickness", "test_method")
    score = sum(fuzzy_match(record.get(field), expected.get(field)) for field in fields)
    return (score + int(same_measurement(record, expected))) / 7


def evaluate_paper(entry, expected_list):
    result = entry.get("result")
    schema_ok = (
        entry.get("success") and isinstance(result, dict)
        and isinstance(result.get("article_title"), str)
        and isinstance(result.get("registros"), list)
        and all(isinstance(r, dict) and isinstance(r.get("polymer"), str)
                and bool(r["polymer"].strip()) and isinstance(r.get("wvtr_units"), str)
                and bool(r["wvtr_units"].strip()) and normalize_number(r.get("wvtr_value")) is not None
                for r in result["registros"])
    )
    records = result["registros"] if schema_ok else []
    # Maximum bipartite matching prevents a greedy match from hiding a valid pair.
    candidates = [
        sorted((i for i, r in enumerate(records) if same_measurement(r, exp)),
               key=lambda i: matchRegistro(records[i], exp), reverse=True)
        for exp in expected_list
    ]
    assigned = {}

    def assign(expected_index, visited):
        for record_index in candidates[expected_index]:
            if record_index in visited:
                continue
            visited.add(record_index)
            if record_index not in assigned or assign(assigned[record_index], visited):
                assigned[record_index] = expected_index
                return True
        return False

    for index in range(len(expected_list)):
        assign(index, set())
    matched = len(assigned)
    correct_empty = bool(schema_ok and not records and not expected_list)
    return {
        "json_valid": bool(entry.get("success") and isinstance(result, dict)),
        "schema_compliant": bool(schema_ok),
        "recall": round(matched / len(expected_list), 3) if expected_list else float(correct_empty),
        "precision": round(matched / len(records), 3) if records else float(correct_empty),
        "field_accuracy": round(sum(matchRegistro(records[r], expected_list[e])
                                    for r, e in assigned.items()) / matched, 3) if matched else float(correct_empty),
        "n_expected": len(expected_list), "n_found": len(records),
    }


def evaluate_results(results, truth):
    summary = {}
    for model, entries in results.items():
        scores, unannotated = [], []
        for entry in entries:
            paper = entry["paper"]
            annotation = truth.get(paper)
            if not isinstance(annotation, dict) or "expected" not in annotation:
                unannotated.append(paper)
                continue
            expected = annotation["expected"]
            if not isinstance(expected, list) or any(
                    not isinstance(row, dict) or not row.get("polymer")
                    or not row.get("wvtr_units") or normalize_number(row.get("wvtr_value")) is None
                    for row in expected):
                raise ValueError(f"Anotación inválida para {paper}")
            score = evaluate_paper(entry, expected)
            scores.append({"paper": paper, **score})
        aggregate = {
            "evaluated_papers": len(scores), "total_papers": len(entries),
            "unannotated_papers": unannotated, "papers": scores,
        }
        for field in ("recall", "precision", "field_accuracy", "schema_compliant", "json_valid"):
            aggregate[f"avg_{field}"] = (
                round(sum(s[field] for s in scores) / len(scores), 3) if scores else None)
        summary[model] = aggregate
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("benchmark/results.json"))
    parser.add_argument("--ground-truth", type=Path, default=Path("benchmark/ground_truth.json"))
    parser.add_argument("--output", type=Path, default=Path("temp/benchmark/evaluation.json"))
    args = parser.parse_args(argv)
    try:
        results = json.loads(args.results.read_text(encoding="utf-8"))
        truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))
        summary = evaluate_results(results, truth)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    for model, metrics in summary.items():
        print(f"{model}: {metrics['evaluated_papers']}/{metrics['total_papers']} artículos evaluados; "
              f"recall={metrics['avg_recall']}, precision={metrics['avg_precision']}")
        if metrics["unannotated_papers"]:
            print("Sin anotación: " + ", ".join(metrics["unannotated_papers"]))
    return 0 if any(m["evaluated_papers"] for m in summary.values()) else 1


def run_evaluation():
    return main([])


if __name__ == "__main__":
    raise SystemExit(main())
