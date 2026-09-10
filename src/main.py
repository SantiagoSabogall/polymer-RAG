import sys
sys.path.insert(0, "src")

from cloudfareR2 import get_s3_client, list_pdfs, download_pdf
from pdf_to_markdown import pdf_to_markdown

# 1. Conectar a R2
s3 = get_s3_client()

# 2. Listar PDFs
pdfs = list_pdfs(s3)
print(f"PDFs encontrados: {pdfs}")

# 3. Descargar y procesar TODOS los PDFs
for pdf_key in pdfs:
    nombre = pdf_key.split("/")[-1]          # quitar prefijo "pdfs/"
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