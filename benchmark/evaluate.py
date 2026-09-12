import json
import os

BENCHMARK_DIR = os.path.dirname(__file__)
RESULTS_FILE = os.path.join(BENCHMARK_DIR, "results.json")
GROUND_TRUTH_FILE = os.path.join(BENCHMARK_DIR, "ground_truth.json")


def fuzzy_match(a, b):
    if a is None or b is None:
        return a == b
    a_str = str(a).strip().lower()
    b_str = str(b).strip().lower()
    return a_str == b_str


def normalize_number(val):
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def matchRegistro(reg, expected):
    score = 0
    total = 0

    if fuzzy_match(reg.get("polymer"), expected.get("polymer")):
        score += 1
    total += 1

    reg_val = normalize_number(reg.get("wvtr_value"))
    exp_val = normalize_number(expected.get("wvtr_value"))
    if reg_val is not None and exp_val is not None:
        if abs(reg_val - exp_val) / max(abs(exp_val), 0.001) < 0.1:
            score += 1
    elif reg_val == exp_val:
        score += 1
    total += 1

    for field in ["wvtr_units", "temperature", "rh", "thickness", "test_method"]:
        if fuzzy_match(reg.get(field), expected.get(field)):
            score += 1
        total += 1

    return score / total if total > 0 else 0


def evaluate_paper(result_entry, expected_list):
    if not result_entry.get("success") or result_entry.get("result") is None:
        return {
            "json_valid": False,
            "schema_compliant": False,
            "recall": 0.0,
            "precision": 0.0,
            "field_accuracy": 0.0,
            "n_expected": len(expected_list),
            "n_found": 0,
        }

    result = result_entry["result"]
    registros = result.get("registros", [])

    json_valid = True

    schema_ok = "article_title" in result and "registros" in result
    if schema_ok:
        for reg in registros:
            if "polymer" not in reg or "wvtr_value" not in reg:
                schema_ok = False
                break

    if not expected_list:
        return {
            "json_valid": json_valid,
            "schema_compliant": schema_ok,
            "recall": 1.0 if not registros else 0.0,
            "precision": 0.0,
            "field_accuracy": 1.0,
            "n_expected": 0,
            "n_found": len(registros),
        }

    matched = []
    used = set()
    for exp in expected_list:
        best_score = 0
        best_idx = -1
        for i, reg in enumerate(registros):
            if i in used:
                continue
            s = matchRegistro(reg, exp)
            if s > best_score:
                best_score = s
                best_idx = i
        if best_idx >= 0:
            matched.append(best_score)
            used.add(best_idx)

    recall = len(matched) / len(expected_list) if expected_list else 1.0
    precision = len(matched) / len(registros) if registros else 0.0
    field_accuracy = sum(matched) / len(matched) if matched else 0.0

    return {
        "json_valid": json_valid,
        "schema_compliant": schema_ok,
        "recall": round(recall, 3),
        "precision": round(precision, 3),
        "field_accuracy": round(field_accuracy, 3),
        "n_expected": len(expected_list),
        "n_found": len(registros),
    }


def run_evaluation():
    if not os.path.exists(RESULTS_FILE):
        print("No existe results.json. Ejecuta run_benchmark.py primero.")
        return

    if not os.path.exists(GROUND_TRUTH_FILE):
        print("No existe ground_truth.json.")
        print("Crea el archivo con los valores esperados para cada paper.")
        print("Ejemplo:")
        print(json.dumps({
            "paper_1.md": {
                "expected": [
                    {
                        "polymer": "EVOH",
                        "wvtr_value": 4.5,
                        "wvtr_units": "g/m2/day",
                        "temperature": "38C",
                        "rh": "90%",
                        "thickness": "15 um",
                        "test_method": "ASTM F1249"
                    }
                ]
            }
        }, indent=2))
        return

    with open(RESULTS_FILE, "r") as f:
        results = json.load(f)

    with open(GROUND_TRUTH_FILE, "r") as f:
        ground_truth = json.load(f)

    summary = {}

    for model_name, model_results in results.items():
        model_scores = []

        for entry in model_results:
            paper_name = entry["paper"]
            expected = ground_truth.get(paper_name, {}).get("expected", [])

            scores = evaluate_paper(entry, expected)
            scores["paper"] = paper_name
            scores["cost"] = entry.get("cost", 0)
            scores["latency"] = entry.get("latency", 0)
            model_scores.append(scores)

        if not model_scores:
            continue

        avg = {
            "json_valid_rate": sum(s["json_valid"] for s in model_scores) / len(model_scores),
            "schema_compliance_rate": sum(s["schema_compliant"] for s in model_scores) / len(model_scores),
            "avg_recall": sum(s["recall"] for s in model_scores) / len(model_scores),
            "avg_precision": sum(s["precision"] for s in model_scores) / len(model_scores),
            "avg_field_accuracy": sum(s["field_accuracy"] for s in model_scores) / len(model_scores),
            "total_cost": sum(s["cost"] for s in model_scores),
            "avg_latency": sum(s["latency"] for s in model_scores) / len(model_scores),
            "papers": model_scores,
        }
        summary[model_name] = avg

    eval_file = os.path.join(BENCHMARK_DIR, "evaluation.json")
    with open(eval_file, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 80)
    print("  RESULTADOS DEL BENCHMARK")
    print("=" * 80)
    print(f"{'Modelo':<20} {'JSON':>6} {'Schema':>8} {'Recall':>8} {'Precision':>10} {'Accuracy':>10} {'Cost':>10} {'Latency':>8}")
    print("-" * 80)

    for model_name, avg in summary.items():
        print(f"{model_name:<20} "
              f"{avg['json_valid_rate']*100:>5.0f}% "
              f"{avg['schema_compliance_rate']*100:>7.0f}% "
              f"{avg['avg_recall']:>7.3f} "
              f"{avg['avg_precision']:>9.3f} "
              f"{avg['avg_field_accuracy']:>9.3f} "
              f"${avg['total_cost']:>9.4f} "
              f"{avg['avg_latency']:>7.1f}s")

    print("=" * 80)
    print(f"\nEvaluación guardada en: {eval_file}")


if __name__ == "__main__":
    run_evaluation()
