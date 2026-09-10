import os
import pathlib
from dotenv import load_dotenv


_config_dir = pathlib.Path(__file__).resolve().parent
load_dotenv(_config_dir.parent / "API_KEY.env")


URL_BASE = "http://localhost:20128/v1"
API_KEY = os.getenv("OMNIROUTE_API")

OPENROUTER_API_KEY = os.getenv("OPENROUTE_API")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")


PG_CONNECTION = os.getenv("PG_CONNECTION")
PG_HOST = os.getenv("PG_HOST")
PG_PORT = os.getenv("PG_PORT", "5432")

PG_DATABASE = os.getenv("PG_DATABASE")
PG_USER = os.getenv("PG_USER")
PG_PASSWORD =  os.getenv("PG_PASSWORD")

