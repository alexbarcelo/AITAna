"""MinIO (S3-compatible) wrapper for storing/retrieving raw submission files.

Format-agnostic on purpose: a submission's raw file (PDF, notebook, ...) is
always stored as the exact bytes uploaded, under a key whose extension comes
from the owning rubric's `SubmissionFormat` (see `grading/extraction`'s
`FORMAT_FILE_INFO`) -- this module itself has no PDF-specific logic.
"""

from __future__ import annotations

import logging
from pathlib import Path

from minio import Minio

from .settings import get_settings

logger = logging.getLogger(__name__)

_client: Minio | None = None


def get_client() -> Minio:
    global _client
    if _client is None:
        settings = get_settings()
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
    return _client


def ensure_bucket() -> None:
    """Idempotently create the configured bucket. Call once at process startup."""
    client = get_client()
    bucket = get_settings().minio_bucket
    if not client.bucket_exists(bucket):
        logger.info("Creating MinIO bucket %r", bucket)
        client.make_bucket(bucket)


def object_key(rubric_slug: str, student_id: str, submission_id: str, extension: str) -> str:
    return f"{rubric_slug}/{student_id}/{submission_id}{extension}"


def batch_object_key(rubric_slug: str, submission_id: str, extension: str) -> str:
    """Like `object_key`, for a batch-created submission that has no student
    to namespace by yet (see `Submission.student`'s docstring)."""
    return f"{rubric_slug}/_batch/{submission_id}{extension}"


def upload_file(key: str, data: bytes, content_type: str) -> None:
    import io

    client = get_client()
    bucket = get_settings().minio_bucket
    client.put_object(bucket, key, io.BytesIO(data), length=len(data), content_type=content_type)
    logger.info("Uploaded %d byte(s) to %s/%s", len(data), bucket, key)


def download_to_path(key: str, dest: Path) -> None:
    client = get_client()
    bucket = get_settings().minio_bucket
    client.fget_object(bucket, key, str(dest))
    logger.debug("Downloaded %s/%s to %s", bucket, key, dest)


def download_bytes(key: str) -> bytes:
    """Read an object fully into memory -- used to serve a submission's raw
    file back through the API (see api/routers/submissions.py's download
    endpoint) rather than a MinIO presigned URL, since `MINIO_ENDPOINT` is typically an
    internal-only hostname (e.g. `minio:9000` on the docker-compose network)
    that a browser outside that network can't resolve."""
    client = get_client()
    bucket = get_settings().minio_bucket
    response = client.get_object(bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()
