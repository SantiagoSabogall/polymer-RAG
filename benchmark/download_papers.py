import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cloudfareR2 import get_s3_client, list_pdfs, download_pdf
from pdf_to_markdown import pdf_to_markdown
from clean_markdown import clean_markdown, extract_doi
from pathlib import Path

TEMP_PDFS = "temp/benchmark_pdfs"
TEMP_MD = "temp/benchmark_md"
BENCHMARK_PAPERS = os.path.join(os.path.dirname(__file__), "papers")


def download_and_convert(max_papers=7):
    Path(TEMP_PDFS).mkdir(parents=True, exist_ok=True)
    Path(TEMP_MD).mkdir(parents=True, exist_ok=True)
    Path(BENCHMARK_PAPERS).mkdir(parents=True, exist_ok=True)

    s3 = get_s3_client()
    pdfs = list_pdfs(s3)
    pdfs = pdfs[:max_papers]

    print(f"Descargando {len(pdfs)} papers de R2...")

    for i, pdf_key in enumerate(pdfs):
        nombre = pdf_key.split("/")[-1]
        local_pdf = f"{TEMP_PDFS}/{nombre}"

        try:
            print(f"  [{i+1}/{len(pdfs)}] {nombre}...", end=" ")

            download_pdf(s3, pdf_key, local_pdf)
            md_path = pdf_to_markdown(local_pdf, TEMP_MD)

            with open(md_path, "r") as f:
                raw_md = f.read()

            doi = extract_doi(raw_md)
            cleaned_md = clean_markdown(raw_md)

            nombre_md = Path(md_path).stem
            final_path = os.path.join(BENCHMARK_PAPERS, f"{nombre_md}.md")
            with open(final_path, "w") as f:
                f.write(cleaned_md)

            print(f"OK (DOI: {doi or 'N/A'})")

        except Exception as e:
            print(f"ERROR: {e}")

        finally:
            if os.path.exists(local_pdf):
                try:
                    os.remove(local_pdf)
                except OSError:
                    pass

    print(f"\nMarkdowns guardados en: {BENCHMARK_PAPERS}")
    print(f"Archivos: {os.listdir(BENCHMARK_PAPERS)}")


if __name__ == "__main__":
    download_and_convert()
