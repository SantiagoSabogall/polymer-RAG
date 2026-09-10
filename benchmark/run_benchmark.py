import sys
import os
import json
import time
import logging
from pathlib import Path
from openai import OpenAI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
from prompt import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from llm import parse_llm_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MODELS = {
    "deepseek-v4-flash": "deepseek/deepseek-v4-flash-0731",
    "glm-5.3-flash": "z-ai/glm-5.3-flash",
    "gpt-5.6-luna": "openai/gpt-5.6-luna",
    "gemini-3.8-flash": "google/gemini-3.8-flash",
}

PRICING = {
    "deepseek/deepseek-v4-flash-0731": {"input": 0.05, "output": 0.10},
    "z-ai/glm-5.3-flash": {"input": 0.075, "output": 0.25},
    "openai/gpt-5.6-luna": {"input": 0.20, "output": 1.20},
    "google/gemini-3.8-flash": {"input": 0.75, "output": 3.75},
}

PAPERS_DIR = os.path.join(os.path.dirname(__file__), "papers")
RESULTS_FILE = os.path.join(os.path.dirname(__file__), "results.json")


def get_client():
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)


def load_papers():
    papers = []
    for f in sorted(Path(PAPERS_DIR).glob("*.md")):
        with open(f, "r") as fh:
            content = fh.read()
        papers.append({"filename": f.name, "content": content})
    return papers


def extract_wvtr(model_id, paper, client):
    user_prompt = USER_PROMPT_TEMPLATE.format(
        doi="N/A (benchmark)",
        pdf_filename=paper["filename"],
        markdown_content=paper["content"]
    )

    start = time.time()
    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        latency = time.time() - start

        content = response.choices[0].message.content
        usage = response.usage

        result = parse_llm_json(content)

        cost_input = (usage.prompt_tokens / 1_000_000) * PRICING[model_id]["input"]
        cost_output = (usage.completion_tokens / 1_000_000) * PRICING[model_id]["output"]
        total_cost = cost_input + cost_output

        return {
            "success": True,
            "result": result,
            "latency": round(latency, 2),
            "tokens_input": usage.prompt_tokens,
            "tokens_output": usage.completion_tokens,
            "cost": round(total_cost, 6),
            "raw_response": content,
        }

    except Exception as e:
        latency = time.time() - start
        return {
            "success": False,
            "error": str(e),
            "latency": round(latency, 2),
            "result": None,
            "raw_response": None,
        }


def save_results_incremental(results):
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


def run_benchmark():
    client = get_client()
    papers = load_papers()
    print(f"Papers encontrados: {len(papers)}")
    print(f"Modelos a probar: {list(MODELS.keys())}")

    results = {}

    for model_name, model_id in MODELS.items():
        print(f"\n{'='*50}")
        print(f"  Modelo: {model_name} ({model_id})")
        print(f"{'='*50}")

        model_results = []

        for i, paper in enumerate(papers):
            print(f"  [{i+1}/{len(papers)}] {paper['filename']}...", end=" ", flush=True)

            output = extract_wvtr(model_id, paper, client)

            record = {
                "paper": paper["filename"],
                "model": model_name,
                "model_id": model_id,
                **output,
            }
            model_results.append(record)

            if output["success"]:
                n_registros = len(output["result"].get("registros", []))
                print(f"OK ({n_registros} registros, ${output['cost']:.4f}, {output['latency']}s)")
            else:
                print(f"ERROR: {output['error']}")

            time.sleep(1)

        results[model_name] = model_results
        save_results_incremental(results)

        total_cost = sum(r.get("cost", 0) for r in model_results)
        total_latency = sum(r.get("latency", 0) for r in model_results)
        success_count = sum(1 for r in model_results if r.get("success"))
        print(f"\n  Resumen {model_name}: {success_count}/{len(papers)} exitosos, "
              f"costo total: ${total_cost:.4f}, tiempo total: {total_latency:.1f}s")

    print(f"\nResultados guardados en: {RESULTS_FILE}")
    return results


if __name__ == "__main__":
    run_benchmark()
