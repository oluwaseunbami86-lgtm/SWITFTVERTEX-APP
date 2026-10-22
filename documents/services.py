"""Document storage operations.

Order of operations for every upload (so a failure can never leave a false record):
  1. validate the file and compute its SHA-256 (nothing stored yet)
  2. write it to the object store and VERIFY it (size, and a read-back hash up to a size limit)
  3. only then write the database rows + audit event in ONE transaction
  4. if step 3 fails, delete the object again; if THAT fails, log loudly (reconcile_storage finds it)
"""
import logging
import re
import unicodedata
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_event

from .models import Document, DocumentVersion, ReplacedFile
from .storage import StorageError, get_store

log = logging.getLogger("swiftvertex")
A = AuditEvent.Action

CATEGORY_FOLDER = {
    "drawing": "cad", "coordinates": "coordinates", "photo": "photos", "pdf_report": "reports",
    "field_data": "field-data", "deliverable": "deliverables", "other": "documents",
}


class DocumentError(Exception):
    """An operation was refused or failed. The message is shown to the user as-is."""


def safe_name(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^A-Za-z0-9._() -]+", "_", name).strip(" .") or "file"
    stem, dot, ext = name.rpartition(".")
    if not dot:
        return name[:100]
    return f"{stem[:100]}.{ext[:10]}"


def build_key(job, doc_type, filename):
    folder = CATEGORY_FOLDER.get(doc_type, "documents")
    return f"jobs/{job.job_date.year}/{job.plan_number}/{folder}/{uuid.uuid4().hex[:12]}-{safe_name(filename)}"


def store_object(uploaded, key, info):
    """Write to the store and verify. Raises DocumentError (and leaves nothing behind) on any problem."""
    store = get_store()
    try:
        uploaded.seek(0)
        store.put(uploaded, key, size=info.size, sha256=info.sha256, content_type=info.mime)
        stored = store.stat(key)
        if stored != info.size:
            raise StorageError(f"stored size {stored} does not match uploaded size {info.size}")
        if info.size <= settings.VERIFY_READBACK_MAX_MB * 1024 * 1024:
            if store.sha256(key) != info.sha256:
                raise StorageError("checksum of the stored file does not match the uploaded file")
    except StorageError as exc:
        log.error("Upload verification failed for %s: %s", key, exc)
        _discard(key)
        raise DocumentError(
            "FILE UPLOAD FAILED - THE FILE WAS NOT SUCCESSFULLY STORED. Nothing was saved. "
            "Please try again; if it keeps failing, tell an administrator."
        ) from exc


def _discard(key):
    try:
        get_store().delete(key)
    except StorageError:
        log.error("ORPHANED OBJECT: could not remove %s - run `manage.py reconcile_storage`", key)


def _version_fields(info, key, note, user):
    return dict(
        original_filename=info.filename, extension=info.extension, mime_type=info.mime, size_bytes=info.size,
        sha256=info.sha256, storage_key=key, change_note=note[:500], uploaded_by=user,
        storage_backend=DocumentVersion.Backend.OBJECT_STORE, integrity_status=DocumentVersion.Integrity.OK,
        last_verified_at=timezone.now(),
    )


def _run_db(key, func):
    """Run the database half; if it fails, remove the object we just stored."""
    try:
        with transaction.atomic():
            return func()
    except Exception:
        log.exception("Database write failed after storing %s - removing the stored object", key)
        _discard(key)
        raise


def find_identical(job, sha256, exclude_document=None):
    qs = DocumentVersion.objects.filter(document__job=job, document__lifecycle="active", sha256=sha256)
    return qs.select_related("document").first()


def find_same_name(job, filename):
    """The active document in this job whose current file has the same name (case-insensitive)."""
    return (Document.objects.filter(job=job, lifecycle="active",
                                    versions__is_current=True, versions__original_filename__iexact=filename)
            .distinct().first())


def add_document(*, job, uploaded, info, doc_type, title, description, note, user, request=None):
    key = build_key(job, doc_type, info.filename)
    store_object(uploaded, key, info)

    def db():
        doc = Document.objects.create(job=job, doc_type=doc_type, title=title, description=description, uploaded_by=user)
        ver = DocumentVersion.objects.create(document=doc, version_number=1, is_current=True,
                                             **_version_fields(info, key, note, user))
        log_event(user=user, action=A.DOCUMENT_UPLOADED, entity_type="document", entity_id=doc.pk,
                  entity_label=info.filename, new={"job": job.plan_number, "version": 1, "sha256": info.sha256,
                                                   "size": info.size, "key": key},
                  description=f"Uploaded {info.filename} to {job.plan_number}", request=request)
        return doc, ver

    return _run_db(key, db)


def add_version(*, document_pk, uploaded, info, note, user, request=None):
    doc0 = Document.objects.select_related("job").get(pk=document_pk)
    key = build_key(doc0.job, doc0.doc_type, info.filename)
    store_object(uploaded, key, info)

    def db():
        doc = Document.objects.select_for_update().get(pk=document_pk)
        if doc.lifecycle != Document.Lifecycle.ACTIVE:
            raise DocumentError("THIS DOCUMENT IS DELETED - restore it before adding versions.")
        last = doc.versions.order_by("-version_number").first()
        old_current = doc.versions.filter(is_current=True).first()
        if old_current:
            DocumentVersion.objects.filter(pk=old_current.pk).update(is_current=False)
        ver = DocumentVersion.objects.create(document=doc, version_number=(last.version_number + 1) if last else 1,
                                             is_current=True, **_version_fields(info, key, note, user))
        log_event(user=user, action=A.DOCUMENT_VERSION_CREATED, entity_type="document", entity_id=doc.pk,
                  entity_label=info.filename,
                  old={"version": old_current.version_number if old_current else None,
                       "sha256": old_current.sha256 if old_current else None},
                  new={"version": ver.version_number, "sha256": info.sha256, "size": info.size, "key": key},
                  description=f"New version {ver.version_number} of {info.filename}", request=request)
        return doc, ver

    return _run_db(key, db)


def overwrite_current(*, document_pk, uploaded, info, note, user, request=None):
    """Replace the CURRENT version's content in place (no new version number). The previous
    content is kept for OVERWRITE_RETENTION_DAYS so a mistake can still be undone."""
    doc0 = Document.objects.select_related("job").get(pk=document_pk)
    key = build_key(doc0.job, doc0.doc_type, info.filename)
    store_object(uploaded, key, info)

    def db():
        doc = Document.objects.select_for_update().get(pk=document_pk)
        if doc.lifecycle != Document.Lifecycle.ACTIVE:
            raise DocumentError("THIS DOCUMENT IS DELETED - restore it before changing it.")
        ver = doc.versions.select_for_update().filter(is_current=True).first()
        if ver is None:
            raise DocumentError("THIS DOCUMENT HAS NO CURRENT VERSION TO OVERWRITE.")
        ReplacedFile.objects.create(
            version=ver, storage_key=ver.storage_key, original_filename=ver.original_filename,
            size_bytes=ver.size_bytes, sha256=ver.sha256, replaced_by=user,
            purge_after=timezone.now() + timedelta(days=settings.OVERWRITE_RETENTION_DAYS))
        old = {"sha256": ver.sha256, "size": ver.size_bytes, "key": ver.storage_key, "filename": ver.original_filename}
        for field, value in _version_fields(info, key, note or "Overwritten (confirmed)", ver.uploaded_by).items():
            setattr(ver, field, value)
        ver.overwritten_at, ver.overwritten_by = timezone.now(), user
        ver.save()
        log_event(user=user, action=A.DOCUMENT_OVERWRITTEN, entity_type="document", entity_id=doc.pk,
                  entity_label=info.filename, old=old,
                  new={"sha256": info.sha256, "size": info.size, "key": key, "version": ver.version_number},
                  description=(f"Overwrote version {ver.version_number} of {info.filename}; previous content kept "
                               f"for {settings.OVERWRITE_RETENTION_DAYS} days"), request=request)
        return doc, ver

    return _run_db(key, db)


@transaction.atomic
def make_current(*, version_pk, user, request=None):
    ver0 = DocumentVersion.objects.get(pk=version_pk)
    doc = Document.objects.select_for_update().get(pk=ver0.document_id)
    ver = DocumentVersion.objects.get(pk=version_pk)
    if doc.lifecycle != Document.Lifecycle.ACTIVE:
        raise DocumentError("THIS DOCUMENT IS DELETED.")
    if ver.is_current:
        raise DocumentError("THIS VERSION IS ALREADY THE CURRENT ONE.")
    prev = doc.versions.filter(is_current=True).first()
    if prev:
        DocumentVersion.objects.filter(pk=prev.pk).update(is_current=False)
    DocumentVersion.objects.filter(pk=ver.pk).update(is_current=True)
    log_event(user=user, action=A.DOCUMENT_VERSION_RESTORED, entity_type="document", entity_id=doc.pk,
              entity_label=ver.original_filename,
              old={"version": prev.version_number if prev else None}, new={"version": ver.version_number},
              description=f"Version {ver.version_number} of {ver.original_filename} made current", request=request)


def verify_version(version, *, user=None, request=None):
    """Check the stored object against the database record. Returns the resulting status and
    ALWAYS records it; a problem is logged and written to the audit trail."""
    if version.storage_backend != DocumentVersion.Backend.OBJECT_STORE:
        return version.integrity_status
    store = get_store()
    status, detail = DocumentVersion.Integrity.OK, ""
    try:
        size = store.stat(version.storage_key)
        if size is None:
            status, detail = DocumentVersion.Integrity.MISSING, "the file is not in storage"
        elif size != version.size_bytes:
            status, detail = DocumentVersion.Integrity.CORRUPT, f"size is {size} bytes, expected {version.size_bytes}"
        elif version.sha256 and store.sha256(version.storage_key) != version.sha256:
            status, detail = DocumentVersion.Integrity.CORRUPT, "SHA-256 does not match the recorded checksum"
    except StorageError as exc:
        # Storage unreachable is NOT proof of damage: report it, but do not change the stored status.
        log.error("Integrity check could not reach storage for version %s: %s", version.pk, exc)
        raise DocumentError("STORAGE COULD NOT BE REACHED - the file could not be checked. Nothing was changed.") from exc
    DocumentVersion.objects.filter(pk=version.pk).update(integrity_status=status, last_verified_at=timezone.now())
    version.integrity_status = status
    if status != DocumentVersion.Integrity.OK:
        log.error("INTEGRITY FAILURE %s v%s (%s): %s", version.original_filename, version.version_number,
                  version.storage_key, detail)
        log_event(user=user, action=A.FILE_INTEGRITY_FAILED, entity_type="document", entity_id=version.document_id,
                  entity_label=version.original_filename, new={"status": status, "detail": detail,
                                                               "version": version.version_number},
                  description=f"{version.original_filename} v{version.version_number}: {detail}", request=request)
    return status


@transaction.atomic
def soft_delete_document(*, document_pk, user, request=None):
    doc = Document.objects.select_for_update().get(pk=document_pk)
    if doc.lifecycle != Document.Lifecycle.ACTIVE:
        raise DocumentError("THIS DOCUMENT IS ALREADY DELETED.")
    doc.lifecycle, doc.deleted_at, doc.deleted_by = Document.Lifecycle.DELETED, timezone.now(), user
    doc.save()
    log_event(user=user, action=A.DOCUMENT_DELETED, entity_type="document", entity_id=doc.pk, entity_label=doc.filename,
              old={"lifecycle": "active"}, new={"lifecycle": "deleted"},
              description=f"Deleted {doc.filename} from {doc.job.plan_number} (versions kept; can be restored)", request=request)


@transaction.atomic
def restore_document(*, document_pk, user, request=None):
    doc = Document.objects.select_for_update().get(pk=document_pk)
    if doc.lifecycle == Document.Lifecycle.ACTIVE:
        raise DocumentError("THIS DOCUMENT IS ALREADY ACTIVE.")
    doc.lifecycle, doc.deleted_at, doc.deleted_by = Document.Lifecycle.ACTIVE, None, None
    doc.save()
    log_event(user=user, action=A.DOCUMENT_RESTORED, entity_type="document", entity_id=doc.pk, entity_label=doc.filename,
              old={"lifecycle": "deleted"}, new={"lifecycle": "active"}, description=f"Restored {doc.filename}", request=request)


def purge_document_rows(doc):
    """Delete a document's database rows and return the object keys to remove AFTER commit."""
    keys = []
    for ver in doc.versions.all():
        keys.append(ver.storage_key) if ver.storage_backend == DocumentVersion.Backend.OBJECT_STORE else None
        for rep in ver.replaced_files.filter(purged_at__isnull=True):
            keys.append(rep.storage_key)
        ver.replaced_files.all().delete()
    doc.versions.all().delete()
    doc.delete()
    return keys


def remove_objects_after_commit(keys):
    def run():
        for key in keys:
            try:
                get_store().delete(key)
            except StorageError:
                log.error("ORPHANED OBJECT after purge: %s - run `manage.py reconcile_storage`", key)

    transaction.on_commit(run)


@transaction.atomic
def purge_document(*, document_pk, user, typed, request=None):
    doc = Document.objects.select_for_update().get(pk=document_pk)
    if doc.lifecycle != Document.Lifecycle.DELETED:
        raise DocumentError("ONLY DELETED DOCUMENTS CAN BE PERMANENTLY DESTROYED.")
    expected = f"DELETE {doc.filename}"
    if " ".join((typed or "").split()) != " ".join(expected.split()):
        raise DocumentError(f"CONFIRMATION DID NOT MATCH - type exactly: {expected}")
    versions = [{"version": v.version_number, "file": v.original_filename, "sha256": v.sha256, "key": v.storage_key}
                for v in doc.versions.all()]
    log_event(user=user, action=A.DOCUMENT_PURGED, entity_type="document", entity_id=doc.pk, entity_label=doc.filename,
              old={"job": doc.job.plan_number, "versions": versions},
              description=f"PERMANENTLY deleted {doc.filename} and its {len(versions)} version(s)", request=request)
    keys = purge_document_rows(doc)
    remove_objects_after_commit(keys)
    return len(versions)
