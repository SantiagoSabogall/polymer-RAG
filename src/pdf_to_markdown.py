import opendataloader_pdf as pf
from pathlib import Path


class ConversionError(Exception):
    pass


def pdf_to_markdown(pdf_path: str, output_dir: str) -> str:
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        pf.convert(
            input_path=pdf_path,
            output_dir=output_dir,
            format="markdown",
            reading_order="xycut",
            include_header_footer=False,
            keep_line_breaks=False,
            markdown_with_html=True,
            table_method="default",
            use_struct_tree=True,
            sanitize=True,
            image_output="off"
        )

        nombre = Path(pdf_path).stem
        md_path = Path(output_dir) / f"{nombre}.md"
        return str(md_path)

    except ConversionError:
        raise
    except Exception as e:
        raise ConversionError(f"Error convirtiendo {pdf_path} a markdown: {e}")
