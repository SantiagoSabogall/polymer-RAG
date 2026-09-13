"""
src/main.py - Script simple de descarga y conversión de PDFs
============================================================
Descarga PDFs desde Cloudflare R2 y los convierte a Markdown.
Uso: python src/main.py
"""

import sys
import os
from pathlib import Path

# Agregar directorio src al path
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .cloudfareR2 import get_s3_client, list_pdfs, download_pdf
    from .pdf_to_markdown import pdf_to_markdown
except ImportError:
    from cloudfareR2 import get_s3_client, list_pdfs, download_pdf
    from pdf_to_markdown import pdf_to_markdown


def main():
    # 1. Conectar a R2
    s3 = get_s3_client()

    # 2. Listar PDFs
    pdfs = list_pdfs(s3)
    print(f"PDFs encontrados: {len(pdfs)}")

    # 3. Descargar y procesar TODOS los PDFs
    for pdf_key in pdfs:
        nombre = pdf_key.split("/")[-1]
        local_pdf = f"temp/pdfs/{nombre}"

        print(f"\n--- Procesando: {nombre} ---")

        # Descargar
        download_pdf(s3, pdf_key, local_pdf)

        # Convertir a markdown
        md_path = pdf_to_markdown(local_pdf, "temp/markdown")
        print(f"Markdown: {md_path}")

        # Mostrar inicio del markdown
        with open(md_path, "r") as f:
            contenido = f.read(300)
            print(f"Preview:\n{contenido[:300]}...")


if __name__ == "__main__":
    main()
