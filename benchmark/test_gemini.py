import sys
import os
import json
import time
from pathlib import Path
from openai import OpenAI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
from prompt import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from llm import parse_llm_json

MODEL = "google/gemini-3.8-flash"


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def main():
    md_path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "temp" / "markdown_cleaned" / "WVRT PHB.md"
    if not md_path.exists():
        print(f"Error: {md_path} no existe")
        sys.exit(1)

    content = md_path.read_text()
    client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)

    user_prompt = USER_PROMPT_TEMPLATE.format(
        doi="N/A", pdf_filename=md_path.name, markdown_content=content
    )

    start = time.time()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
    )
    latency = time.time() - start

    raw = response.choices[0].message.content
    result = parse_llm_json(raw)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\n[Latencia: {latency:.2f}s | Tokens: {response.usage.prompt_tokens} in / {response.usage.completion_tokens} out]")
    print(f"model:{MODEL}")

if __name__ == "__main__":
    main()
