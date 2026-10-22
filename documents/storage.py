"""Object store for survey files.

Production: Cloudflare R2 (S3 API), private bucket, short-lived signed download URLs.
Development / tests without R2 configured: a private folder on disk (NOT under MEDIA, never
served directly). The database stays the source of truth: every file has a DocumentVersion
row holding its key, size and SHA-256, and nothing here is trusted without checking them.
"""
import hashlib
import logging
import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.http import FileResponse

log = logging.getLogger("swiftvertex")
CHUNK = 1024 * 1024


class StorageError(Exception):
    """The object store could not do what was asked. Never swallowed silently."""


class LocalStore:
    kind = "local"

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key):
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise StorageError("Invalid storage key.")
        return path

    def put(self, fileobj, key, *, size, sha256, content_type):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in iter(lambda: fileobj.read(CHUNK), b""):
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            Path(tmp).unlink(missing_ok=True)
            raise StorageError(f"Could not write file: {exc}") from exc

    def stat(self, key):
        path = self._path(key)
        return path.stat().st_size if path.is_file() else None

    def sha256(self, key):
        digest = hashlib.sha256()
        try:
            with open(self._path(key), "rb") as fh:
                for chunk in iter(lambda: fh.read(CHUNK), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise StorageError(f"Could not read file: {exc}") from exc
        return digest.hexdigest()

    def delete(self, key):
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"Could not delete file: {exc}") from exc

    def list(self, prefix=""):
        for p in self.root.rglob("*"):
            if p.is_file() and not p.name.endswith(".part"):
                key = p.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    yield key, p.stat().st_size

    def download(self, key, filename, content_type, inline=False):
        resp = FileResponse(open(self._path(key), "rb"), as_attachment=not inline, filename=filename,
                            content_type=content_type)
        resp["X-Content-Type-Options"] = "nosniff"
        return resp


class S3Store:
    kind = "r2"

    def __init__(self):
        import boto3
        from botocore.config import Config

        self.bucket = settings.AWS_STORAGE_BUCKET_NAME
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.AWS_S3_ENDPOINT_URL or None,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=getattr(settings, "AWS_S3_REGION_NAME", "auto"),
            config=Config(signature_version="s3v4", connect_timeout=10, read_timeout=120,
                          retries={"max_attempts": 3, "mode": "standard"}, s3={"addressing_style": "path"}),
        )

    def _wrap(self, exc):
        return StorageError(f"Object storage error: {exc}")

    def put(self, fileobj, key, *, size, sha256, content_type):
        from boto3.s3.transfer import TransferConfig
        from botocore.exceptions import BotoCoreError, ClientError

        cfg = TransferConfig(multipart_threshold=64 * 1024 * 1024, multipart_chunksize=32 * 1024 * 1024, max_concurrency=4)
        try:
            self.client.upload_fileobj(fileobj, self.bucket, key, Config=cfg,
                                       ExtraArgs={"ContentType": content_type, "Metadata": {"sha256": sha256}})
        except (BotoCoreError, ClientError) as exc:
            raise self._wrap(exc) from exc

    def stat(self, key):
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise self._wrap(exc) from exc
        except BotoCoreError as exc:
            raise self._wrap(exc) from exc

    def sha256(self, key):
        from botocore.exceptions import BotoCoreError, ClientError

        digest = hashlib.sha256()
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
            for chunk in body.iter_chunks(CHUNK):
                digest.update(chunk)
        except (BotoCoreError, ClientError) as exc:
            raise self._wrap(exc) from exc
        return digest.hexdigest()

    def delete(self, key):
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise self._wrap(exc) from exc

    def list(self, prefix=""):
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    yield obj["Key"], obj["Size"]
        except (BotoCoreError, ClientError) as exc:
            raise self._wrap(exc) from exc

    def download(self, key, filename, content_type, inline=False):
        from django.http import HttpResponseRedirect
        from django.utils.http import content_disposition_header

        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key, "ResponseContentType": content_type,
                    "ResponseContentDisposition": content_disposition_header(not inline, filename)},
            ExpiresIn=settings.DOWNLOAD_URL_SECONDS,
        )
        resp = HttpResponseRedirect(url)
        resp["Cache-Control"] = "private, no-store"
        return resp


_store = None


def get_store():
    global _store
    if _store is None:
        _store = S3Store() if settings.USE_S3 else LocalStore(settings.OBJECT_STORE_ROOT)
    return _store


def reset_store():
    global _store
    _store = None


def hash_stream(fileobj):
    digest, size = hashlib.sha256(), 0
    fileobj.seek(0)
    for chunk in iter(lambda: fileobj.read(CHUNK), b""):
        digest.update(chunk)
        size += len(chunk)
    fileobj.seek(0)
    return digest.hexdigest(), size
