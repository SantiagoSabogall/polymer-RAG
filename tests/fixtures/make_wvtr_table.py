"""Genera una tabla WVTR rasterizada sintética (tipo tabla escaneada de un PDF).

Existe para probar el camino POSITIVO del pipeline con verdad terreno exacta:
sabemos de antemano qué valores hay dentro, así que cualquier valor inventado
por el extractor se detecta. Simula el caso que motivó la ruta de imágenes: una
tabla en la que opendataloader no recupera texto.

    .venv/bin/python tests/fixtures/make_wvtr_table.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# (fila, polímero, valor, unidades, T, RH, espesor, método)
EXPECTED = [
    ("PBAT",            "1.2",   "g/m2/day", "25 C", "50%", "40 um", "ASTM F1249"),
    ("PBAT/PLA",        "2.8",   "g/m2/day", "25 C", "50%", "40 um", "ASTM F1249"),
    ("PBAT + 1% MMT",   "0.6",   "g/m2/day", "25 C", "50%", "40 um", "ASTM F1249"),
    ("PLA",             "6.5",   "g/m2/day", "25 C", "50%", "40 um", "ASTM F1249"),
    ("PHBV",            "3.4",   "g/m2/day", "23 C", "90%", "60 um", "ISO 2528"),
]

HEADERS = ["Sample", "WVTR", "Units", "Temp.", "RH", "Thickness", "Method"]
WIDTHS = [200, 110, 150, 100, 80, 130, 190]


def load_font(size: int):
    for path in (
        "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def build(path="tests/fixtures/wvtr_table_raster.png"):
    font, head_font = load_font(22), load_font(22)
    pad, row_h = 24, 46
    width = sum(WIDTHS) + pad * 2
    height = row_h * (len(EXPECTED) + 2) + pad * 2

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    y = pad
    x = pad
    for header, w in zip(HEADERS, WIDTHS):
        draw.text((x + 8, y), header, fill="black", font=head_font)
        x += w
    y += row_h
    draw.line([(pad, y - 8), (width - pad, y - 8)], fill="black", width=2)

    for row in EXPECTED:
        x = pad
        for value, w in zip(row, WIDTHS):
            draw.text((x + 8, y), value, fill="black", font=font)
            x += w
        y += row_h
        draw.line([(pad, y - 8), (width - pad, y - 8)], fill="#b0b0b0", width=1)

    img.save(path)
    print(f"{path}  {img.width}x{img.height}px  filas={len(EXPECTED)}")
    return path


if __name__ == "__main__":
    build()
