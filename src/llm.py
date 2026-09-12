import json
import re
import time
import logging
from openai import OpenAI
from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
from prompt import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Configuración
MAX_RETRIES = 5
BASE_DELAY = 10
DELAY_BETWEEN_REQUESTS = 3
MODEL = "openai/gpt-5.6-luna"


class LLMError(Exception):
    pass


class RateLimitError(LLMError):
    pass


class APIError(LLMError):
    pass


def parse_llm_json(content):
    content = content.strip()

    # 1. Intento directo
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 2. Remover fencing markdown (```json ... ``` o ``` ... ```)
    fenced = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', content, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3. Buscar primer { hasta último }
    first_brace = content.find('{')
    last_brace = content.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(content[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("No se pudo extraer JSON válido", content, 0)


def get_client():
    try:
        client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
        )
        return client
    except Exception as e:
        raise LLMError(f"Error creando cliente LLM: {e}")


def extract_wvtr(client, markdown_text, doi, pdf_filename):
    user_prompt = USER_PROMPT_TEMPLATE.format(
        doi=doi or "No disponible",
        pdf_filename=pdf_filename,
        markdown_content=markdown_text
    )

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )

        content = response.choices[0].message.content
        result = parse_llm_json(content)

        if "registros" not in result:
            result["registros"] = []

        logger.info(
            f"Extraidos {len(result['registros'])} registros de WVTR"
        )
        return result

    except json.JSONDecodeError as e:
        logger.error(f"LLM no devolvió JSON válido: {e}")
        logger.debug(f"Respuesta cruda: {content[:500]}")
        raise LLMError(f"JSON inválido del LLM: {e}")

    except Exception as e:
        if "429" in str(e) or "rate" in str(e).lower():
            raise RateLimitError(f"Rate limit alcanzado: {e}")
        raise APIError(f"Error en llamada LLM: {e}")


def extract_wvtr_with_retry(client, markdown_text, doi, pdf_filename):
    for attempt in range(MAX_RETRIES):
        try:
            result = extract_wvtr(client, markdown_text, doi, pdf_filename)
            return result

        except RateLimitError as e:
            delay = BASE_DELAY * (2 ** attempt)
            logger.warning(
                f"Rate limit - reintento {attempt + 1}/{MAX_RETRIES}, "
                f"esperando {delay}s..."
            )
            time.sleep(delay)

        except LLMError as e:
            if attempt < MAX_RETRIES - 1:
                logger.warning(
                    f"Error LLM - reintento {attempt + 1}/{MAX_RETRIES}: {e}"
                )
                time.sleep(BASE_DELAY)
            else:
                raise

    raise LLMError(f"Max retries ({MAX_RETRIES}) excedidos")
