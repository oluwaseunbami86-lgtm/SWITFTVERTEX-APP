"""Job lifecycle operations: ACTIVE -> ARCHIVED -> DELETED (soft) -> purged.

Each operation runs in one transaction together with its audit event. If the
audit row cannot be written, the change is rolled back - a destructive action
is never left unrecorded.
"""
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_event

from .models import Job

A = AuditEvent.Action
L = Job.Lifecycle

SNAPSHOT_FIELDS = [
    "survey_type", "job_type", "job_date", "title", "client_id", "project_id", "job_address",
    "survey_area", "area_unit", "plan_number", "has_reference_number", "reference_number",
    "status", "assigned_to_id", "description", "lifecycle",
]


class LifecycleError(Exception):
    """A lifecycle change was refused. The message is safe to show to the user."""


def snapshot(job):
    return {f: ("" if getattr(job, f) is None else str(getattr(job, f))) for f in SNAPSHOT_FIELDS}


def diff(old, new):
    keys = [k for k in new if old.get(k) != new.get(k)]
    return {k: old.get(k) for k in keys}, {k: new.get(k) for k in keys}


def _lock(pk):
    try:
        return Job.all_objects.select_for_update().get(pk=pk)
    except Job.DoesNotExist:
        raise LifecycleError("JOB NOT FOUND - it may already have been permanently deleted.")


@transaction.atomic
def archive_job(pk, user, request=None):
    job = _lock(pk)
    if job.lifecycle != L.ACTIVE:
        raise LifecycleError(f"ONLY ACTIVE JOBS CAN BE ARCHIVED - this job is {job.get_lifecycle_display().lower()}.")
    old = snapshot(job)
    job.lifecycle, job.archived_at, job.archived_by = L.ARCHIVED, timezone.now(), user
    job.row_version += 1
    job.save()
    log_event(user=user, action=A.JOB_ARCHIVED, entity_type="job", entity_id=job.pk, entity_label=job.plan_number,
              old={"lifecycle": old["lifecycle"]}, new={"lifecycle": "archived"},
              description=f"Archived job {job.plan_number}", request=request)
    return job


@transaction.atomic
def restore_job(pk, user, request=None):
    job = _lock(pk)
    if job.lifecycle == L.ACTIVE:
        raise LifecycleError("THIS JOB IS ALREADY ACTIVE.")
    before = job.lifecycle
    job.lifecycle = L.ACTIVE
    job.archived_at = job.archived_by = job.deleted_at = job.deleted_by = None
    job.row_version += 1
    job.save()
    log_event(user=user, action=A.JOB_RESTORED, entity_type="job", entity_id=job.pk, entity_label=job.plan_number,
              old={"lifecycle": before}, new={"lifecycle": "active"},
              description=f"Restored job {job.plan_number}", request=request)
    return job


@transaction.atomic
def soft_delete_job(pk, user, request=None):
    job = _lock(pk)
    if job.lifecycle != L.ARCHIVED:
        raise LifecycleError("ARCHIVE THE JOB FIRST - only archived jobs can be deleted.")
    job.lifecycle, job.deleted_at, job.deleted_by = L.DELETED, timezone.now(), user
    job.row_version += 1
    job.save()
    log_event(user=user, action=A.JOB_SOFT_DELETED, entity_type="job", entity_id=job.pk, entity_label=job.plan_number,
              old={"lifecycle": "archived"}, new={"lifecycle": "deleted"},
              description=f"Deleted job {job.plan_number} (can still be restored by an administrator)", request=request)
    return job


@transaction.atomic
def purge_job(pk, user, typed, request=None):
    """Permanently destroy a job, its beacons and its document records/files.
    Only possible for a soft-deleted job and only with the exact typed phrase."""
    job = _lock(pk)
    if job.lifecycle != L.DELETED:
        raise LifecycleError("ONLY DELETED JOBS CAN BE PERMANENTLY DESTROYED.")
    expected = f"DELETE {job.plan_number}"
    if (typed or "").strip() != expected:
        raise LifecycleError(f"CONFIRMATION DID NOT MATCH - type exactly: {expected}")

    from documents.services import purge_document_rows, remove_objects_after_commit

    docs = list(job.documents.all())
    points = list(job.survey_points.values("beacon_number", "northing", "easting", "height"))
    # The audit record is written first, in this same transaction, and keeps a
    # full description of what was destroyed.
    log_event(
        user=user, action=A.JOB_PURGED, entity_type="job", entity_id=job.pk, entity_label=job.plan_number,
        old={**snapshot(job), "beacons": [{k: str(v) for k, v in p.items()} for p in points],
             "documents": [{"file": d.filename, "versions": d.versions.count()} for d in docs]},
        description=f"PERMANENTLY deleted job {job.plan_number} with {len(points)} beacon(s) and {len(docs)} document(s)",
        request=request,
    )
    keys, legacy = [], []
    for d in docs:
        if d.file:
            legacy.append((d.file.storage, d.file.name))
        keys += purge_document_rows(d)
    job.survey_points.all().delete()
    job.delete()
    remove_objects_after_commit(keys)

    def remove_legacy():
        for storage, name in legacy:
            try:
                storage.delete(name)
            except Exception:
                import logging
                logging.getLogger("swiftvertex").error("Could not remove legacy file %s after purge", name)

    transaction.on_commit(remove_legacy)
    return len(points), len(docs)
