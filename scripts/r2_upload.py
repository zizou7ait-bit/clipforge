import os
import json
import time
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, NoCredentialsError, SSLError, EndpointConnectionError, ConnectionClosedError

RETRYABLE_EXCEPTIONS = (SSLError, EndpointConnectionError, ConnectionClosedError)
MAX_ATTEMPTS = 6
BASE_DELAY_SECONDS = 8  # grows: 8, 16, 24, 32, 40...


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
        config=Config(
            signature_version="s3v4",
            s3={'addressing_style': 'path'},
            retries={"max_attempts": 3, "mode": "standard"}
        ),
    )


def get_public_url(r2_key: str) -> str:
    base_url = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    if not base_url:
        account_id = _get_env_var("R2_ACCOUNT_ID")
        base_url = f"https://pub-{account_id}.r2.dev"
    return f"{base_url}/{r2_key}"


def _run_with_retries(operation_name: str, fn):
    """Runs fn() with retries on transient TLS/connection failures.
    Rebuilds the boto3 client fresh on every attempt, since a stale
    connection can be why the handshake fails again."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"[INFO] {operation_name}: attempt {attempt}/{MAX_ATTEMPTS}")
            result = fn()
            print(f"[SUCCESS] {operation_name} succeeded on attempt {attempt}")
            return result
        except RETRYABLE_EXCEPTIONS as e:
            print(f"[WARN] {operation_name}: retryable error on attempt {attempt}: {e}")
            if attempt == MAX_ATTEMPTS:
                print(f"[ERROR] {operation_name}: all attempts exhausted.")
                raise
            delay = BASE_DELAY_SECONDS * attempt
            print(f"[INFO] Waiting {delay}s before retrying...")
            time.sleep(delay)
        except (ClientError, NoCredentialsError) as e:
            # Auth errors, missing bucket, etc. — retrying won't help.
            print(f"[ERROR] {operation_name}: non-retryable error: {e}")
            raise


def upload_file(local_path: str, r2_key: str) -> str:
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Cannot upload missing file: {local_path}")
    bucket_name = _get_env_var("R2_BUCKET_NAME")
    extra_args = {}
    if r2_key.endswith(".mp4"):
        extra_args["ContentType"] = "video/mp4"
    elif r2_key.endswith(".json"):
        extra_args["ContentType"] = "application/json"

    def _do_upload():
        client = get_r2_client()  # fresh client each attempt
        client.upload_file(local_path, bucket_name, r2_key, ExtraArgs=extra_args)

    _run_with_retries(f"upload_file({r2_key})", _do_upload)
    return get_public_url(r2_key)


def upload_json(data_dict: dict, r2_key: str) -> str:
    bucket_name = _get_env_var("R2_BUCKET_NAME")
    json_bytes = json.dumps(data_dict, indent=2).encode("utf-8")

    def _do_upload():
        client = get_r2_client()
        client.put_object(Bucket=bucket_name, Key=r2_key, Body=json_bytes, ContentType="application/json")

    _run_with_retries(f"upload_json({r2_key})", _do_upload)
    return get_public_url(r2_key)


def download_file(r2_key: str, local_path: str) -> str:
    bucket_name = _get_env_var("R2_BUCKET_NAME")
    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    def _do_download():
        client = get_r2_client()
        client.download_file(bucket_name, r2_key, local_path)

    _run_with_retries(f"download_file({r2_key})", _do_download)
    return local_path


def read_json(r2_key: str) -> dict:
    bucket_name = _get_env_var("R2_BUCKET_NAME")

    def _do_read():
        client = get_r2_client()
        response = client.get_object(Bucket=bucket_name, Key=r2_key)
        return json.loads(response["Body"].read().decode("utf-8"))

    return _run_with_retries(f"read_json({r2_key})", _do_read)
