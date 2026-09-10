import boto3
from pathlib import Path
from config import R2_ACCESS_KEY, R2_BUCKET_NAME, R2_ENDPOINT, R2_SECRET_KEY


class R2ConnectionError(Exception):
    pass


class DownloadError(Exception):
    pass


def get_s3_client():
    try:
        s3_client = boto3.client(
            service_name="s3",
            endpoint_url=R2_ENDPOINT,
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY,
            region_name="auto"
        )
        return s3_client
    except Exception as e:
        raise R2ConnectionError(f"Error conectando a R2: {e}")


def list_pdfs(s3_client):
    try:
        response = s3_client.list_objects_v2(Bucket=R2_BUCKET_NAME)
        contents = response.get("Contents", [])
        pdfs = [obj["Key"] for obj in contents if obj["Key"].endswith(".pdf")]
        return pdfs
    except Exception as e:
        raise R2ConnectionError(f"Error listando PDFs en R2: {e}")


def download_pdf(s3_client, pdf_key, local_path):
    try:
        carpeta = Path(local_path).parent
        carpeta.mkdir(parents=True, exist_ok=True)

        s3_client.download_file(
            Bucket=R2_BUCKET_NAME,
            Key=pdf_key,
            Filename=local_path
        )
        return local_path
    except Exception as e:
        raise DownloadError(f"Error descargando {pdf_key}: {e}")
