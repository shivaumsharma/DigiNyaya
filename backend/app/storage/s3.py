"""S3 storage provider -- mirrors local.py's shape exactly, swapping the
local filesystem for an S3 bucket. See app.storage.factory.StorageFactory
for the selection point (DIGINYAYA_STORAGE_PROVIDER=s3).

Bucket/region come from config (DIGINYAYA_S3_BUCKET) and boto3's normal
credential chain (instance role in production -- see the AWS migration
plan's IAM section -- or local `aws configure` credentials in dev), never
hardcoded here.
"""

from __future__ import annotations

import uuid

import boto3
from botocore.exceptions import ClientError

from .base import BaseStorageProvider
from .config import config
from .sanitize import sanitize_case_id, sanitize_filename


class S3Provider(BaseStorageProvider):
    def __init__(self, bucket: str | None = None) -> None:
        self._bucket = bucket or config.s3_bucket
        if not self._bucket:
            raise ValueError(
                "DIGINYAYA_STORAGE_PROVIDER=s3 requires DIGINYAYA_S3_BUCKET to be set."
            )
        self._client = boto3.client("s3")

    def _key(self, case_id: str, filename: str) -> str:
        safe_case_id = sanitize_case_id(case_id)
        safe_name = sanitize_filename(filename)
        return f"{safe_case_id}/{uuid.uuid4().hex}_{safe_name}"

    def save(self, case_id: str, filename: str, content: bytes) -> str:
        key = self._key(case_id, filename)
        self._client.put_object(Bucket=self._bucket, Key=key, Body=content)
        return key

    def read(self, storage_path: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=storage_path)
        return response["Body"].read()

    def delete(self, storage_path: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=storage_path)

    def exists(self, storage_path: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=storage_path)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return False
            raise
