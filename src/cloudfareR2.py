import boto3
from pathlib import Path
try:
    from .config import R2_ACCESS_KEY, R2_BUCKET_NAME, R2_ENDPOINT, R2_SECRET_KEY
except ImportError:
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
    """Lista solo las keys .pdf (wrapper backward-compatible)."""
    return [obj["key"] for obj in list_pdf_objects(s3_client)]


def list_pdf_objects(s3_client):
    """Lista PDFs con metadata de sincronización (key, etag, last_modified, size).

    El ETag permite detectar PDFs nuevos vs modificados sin descargar.
    """
    try:
        objs = []
        continuation_token = None

        while True:
            kwargs = {"Bucket": R2_BUCKET_NAME}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token

            response = s3_client.list_objects_v2(**kwargs)
            contents = response.get("Contents", [])
            for obj in contents:
                key = obj.get("Key", "")
                if not key.lower().endswith(".pdf"):
                    continue
                objs.append({
                    "key": key,
                    "etag": (obj.get("ETag") or "").strip('"') or None,
                    "last_modified": str(obj.get("LastModified")) if obj.get("LastModified") else None,
                    "size": obj.get("Size"),
                })

            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

        return objs
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
