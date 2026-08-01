import os
import json
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, NoCredentialsError


def _get_env_var(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value


def get_r2_client():
    account_id = _get_env_var("R2_ACCOUNT_ID")
    access_key = _get_env_var("R2_ACCESS_KEY_ID")
    secret_key = _get_env_var("R2_SECRET_ACCESS_KEY")
    endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
    )


def get_public_url(r2_key: str) -> str:
    base_url = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    if not base_url:
        account_id = _get_env_var("R2_ACCOUNT_ID")
        base_url = f"https://pub-{account_id}.r2.dev"
    return f"{base_url}/{r2_key}"


def upload_file(local_path: str, r2_key: str) -> str:
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Cannot upload missing file: {local_path}")

    bucket_name = _get_env_var("R2_BUCKET_NAME")
    client = get_r2_client()

    extra_args = {}
    if r2_key.endswith(".mp4"):
        extra_args["ContentType"] = "video/mp4"
    elif r2_key.endswith(".json"):
        extra_args["ContentType"] = "application/json"

    client.upload_file(local_path, bucket_name, r2_key, ExtraArgs=extra_args)
    return get_public_url(r2_key)


def upload_json(data_dict: dict, r2_key: str) -> str:
    bucket_name = _get_env_var("R2_BUCKET_NAME")
    client = get_r2_client()
    json_bytes = json.dumps(data_dict, indent=2).encode("utf-8")
    client.put_object(Bucket=bucket_name, Key=r2_key, Body=json_bytes, ContentType="application/json")
    return get_public_url(r2_key)


def download_file(r2_key: str, local_path: str) -> str:
    bucket_name = _get_env_var("R2_BUCKET_NAME")
    client = get_r2_client()
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    client.download_file(bucket_name, r2_key, local_path)
    return local_path


def read_json(r2_key: str) -> dict:
    bucket_name = _get_env_var("R2_BUCKET_NAME")
    client = get_r2_client()
    response = client.get_object(Bucket=bucket_name, Key=r2_key)
    return json.loads(response["Body"].read().decode("utf-8"))
