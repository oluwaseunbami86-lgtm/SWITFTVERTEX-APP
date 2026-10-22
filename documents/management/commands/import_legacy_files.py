import hashlib

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db import transaction

from audit.models import AuditEvent
from audit.services import log_event
from documents.models import DocumentVersion
from documents.services import DocumentError, build_key, store_object
from documents.validators import UploadInfo


class Command(BaseCommand):
    help = ("Move files uploaded before versioning (still on local media) into the object store (R2), verifying "
            "size and checksum. The local file is NEVER deleted by this command.")

    def handle(self, *args, **opts):
        moved = failed = 0
        for ver in DocumentVersion.objects.filter(storage_backend="legacy_media").select_related("document__job"):
            name = ver.storage_key
            if not default_storage.exists(name):
                self.stderr.write(self.style.ERROR(f"MISSING locally, cannot import: {name}"))
                failed += 1
                continue
            digest, size = hashlib.sha256(), 0
            with default_storage.open(name, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            if ver.sha256 and ver.sha256 != digest.hexdigest():
                self.stderr.write(self.style.ERROR(f"CHECKSUM CHANGED since migration, not imported: {name}"))
                failed += 1
                continue
            info = UploadInfo(filename=ver.original_filename, extension=ver.extension, mime=ver.mime_type or "application/octet-stream",
                              size=size, sha256=digest.hexdigest())
            key = build_key(ver.document.job, ver.document.doc_type, ver.original_filename)
            try:
                with default_storage.open(name, "rb") as fh:
                    store_object(fh, key, info)
                with transaction.atomic():
                    ver.storage_key, ver.storage_backend = key, DocumentVersion.Backend.OBJECT_STORE
                    ver.size_bytes, ver.sha256, ver.integrity_status = size, info.sha256, DocumentVersion.Integrity.OK
                    ver.save()
                    log_event(user=None, action=AuditEvent.Action.DOCUMENT_UPLOADED, entity_type="document",
                              entity_id=ver.document_id, entity_label=ver.original_filename,
                              old={"key": name, "backend": "legacy_media"}, new={"key": key, "sha256": info.sha256},
                              description="Legacy file moved to object storage and verified")
            except (DocumentError, Exception) as exc:
                self.stderr.write(self.style.ERROR(f"FAILED {name}: {exc}"))
                failed += 1
                continue
            moved += 1
            self.stdout.write(f"moved {name} -> {key}")
        self.stdout.write(f"{moved} file(s) moved, {failed} failure(s). Local originals were left in place.")
        if failed:
            raise SystemExit(1)
