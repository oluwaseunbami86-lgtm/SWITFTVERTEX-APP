import hashlib
import io
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from documents.storage import StorageError, get_store


class Command(BaseCommand):
    help = ("Live round-trip test of the configured object store (R2 when USE_S3=True): write, size check, "
            "read-back checksum, list, delete. Prints GREEN or RED; exits non-zero on any failure.")

    def handle(self, *args, **opts):
        store = get_store()
        where = (f"bucket: {settings.AWS_STORAGE_BUCKET_NAME} | endpoint: {settings.AWS_S3_ENDPOINT_URL}"
                 if store.kind == "r2" else f"folder: {settings.OBJECT_STORE_ROOT}")
        self.stdout.write(f"Store: {store.kind} | {where}")
        key = f"healthcheck/{int(time.time())}-{os.urandom(4).hex()}.txt"
        body = os.urandom(4096)
        sha = hashlib.sha256(body).hexdigest()
        steps = []
        try:
            store.put(io.BytesIO(body), key, size=len(body), sha256=sha, content_type="text/plain")
            steps.append("write")
            if store.stat(key) != len(body):
                raise StorageError("size after write does not match")
            steps.append("size")
            if store.sha256(key) != sha:
                raise StorageError("checksum after read-back does not match")
            steps.append("read-back checksum")
            if key not in dict(store.list("healthcheck/")):
                raise StorageError("object not found in listing")
            steps.append("list")
            store.delete(key)
            if store.stat(key) is not None:
                raise StorageError("object still present after delete")
            steps.append("delete")
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"RED - storage check FAILED after steps {steps}: {exc}"))
            try:
                store.delete(key)
            except Exception:
                pass
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("GREEN - storage is working: " + ", ".join(steps)))
