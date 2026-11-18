"""PDF → Markdown (opendataloader-pdf) con extracción opcional de imágenes.

Dos modos:

- ``with_images=False`` (default, retrocompatible): solo Markdown, idéntico al
  comportamiento histórico. Lo usan ``main.py``, ``src/main.py`` y
  ``benchmark/download_papers.py`` sin cambios.
- ``with_images=True``: además escribe el outline ``<stem>.json`` y rasteriza las
  imágenes a ``<output_dir>/images/<stem>/imageFileN.png``.

Por qué ``image_output="external"`` y no otro valor: el backend (Java) solo
acepta ``off | embedded | external`` — ver ``Config.isValidImageOutput`` en
``opendataloader_pdf/jar/opendataloader-pdf-cli.jar``. Cualquier otro string
lanza ``Unsupported image output mode``. ``external`` escribe archivos sueltos
y deja en el Markdown una referencia ``![alt](...)``, que es justo lo que
necesitamos para recortar el contexto de cada figura.

No se toca ``content_safety_off``: por defecto el backend ya descarta imágenes
``tiny`` / ``off-page`` / ``hidden`` (logos, ruido de maquetación). Desactivar
esos filtros reintroduce el ruido que queremos evitar.
"""

from pathlib import Path

import opendataloader_pdf as pf


class ConversionError(Exception):
    pass


def outline_json_for(md_path) -> Path:
    """Ruta del outline JSON que acompaña a un Markdown dado."""
    return Path(md_path).with_suffix(".json")


def images_dir_for(md_path) -> Path:
    """Directorio de imágenes extraídas para un Markdown dado."""
    md = Path(md_path)
    return md.parent / "images" / md.stem


def pdf_to_markdown(pdf_path: str, output_dir: str, with_images: bool = False) -> str:
    """Convierte un PDF a Markdown y devuelve la ruta del ``.md``.

    Args:
        pdf_path: ruta del PDF de entrada.
        output_dir: directorio de salida.
        with_images: si True, emite también ``<stem>.json`` + ``images/<stem>/``.

    Returns:
        Ruta (str) del Markdown generado.
    """
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        nombre = Path(pdf_path).stem
        images_dir = Path(output_dir) / "images" / nombre
        if with_images:
            images_dir.mkdir(parents=True, exist_ok=True)

        pf.convert(
            input_path=pdf_path,
            output_dir=output_dir,
            format=["markdown", "json"] if with_images else "markdown",
            reading_order="xycut",
            include_header_footer=False,
            keep_line_breaks=False,
            markdown_with_html=True,
            table_method="default",
            use_struct_tree=True,
            sanitize=True,
            image_output="external" if with_images else "off",
            image_dir=str(images_dir) if with_images else None,
        )

        md_path = Path(output_dir) / f"{nombre}.md"
        return str(md_path)

    except ConversionError:
        raise
    except Exception as e:
        raise ConversionError(f"Error convirtiendo {pdf_path} a markdown: {e}")
