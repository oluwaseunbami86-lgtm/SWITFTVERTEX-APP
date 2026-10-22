from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Lightweight record of who touched what and when.

    Kept deliberately simple (path + method) rather than diffing model
    fields, so it works generically across every app without per-model
    wiring. Good enough to answer "who changed something, and when".
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="audit_logs"
    )
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        who = self.user.username if self.user else "anonymous"
        return f"{who} {self.method} {self.path} @ {self.timestamp:%Y-%m-%d %H:%M}"


class AuditEventQuerySet(models.QuerySet):
    def update(self, *a, **k):
        raise PermissionError("Audit history is append-only and cannot be modified.")

    def delete(self):
        raise PermissionError("Audit history is append-only and cannot be deleted.")

    bulk_update = update


class AuditEvent(models.Model):
    """Permanent, append-only record of an important event.

    Application code cannot update or delete rows (see save/delete below), and on
    PostgreSQL a trigger rejects UPDATE/DELETE even from raw SQL. The user is
    stored as an id plus a username snapshot (not a foreign key) so the history
    survives any later change to the user account.
    """

    class Action(models.TextChoices):
        JOB_CREATED = "job_created", "Job created"
        JOB_UPDATED = "job_updated", "Job updated"
        JOB_STATUS_CHANGED = "job_status_changed", "Job status changed"
        JOB_ARCHIVED = "job_archived", "Job archived"
        JOB_RESTORED = "job_restored", "Job restored"
        JOB_SOFT_DELETED = "job_soft_deleted", "Job deleted (recoverable)"
        JOB_PURGED = "job_purged", "Job permanently deleted"
        BEACONS_CHANGED = "beacons_changed", "Survey beacons changed"
        CLIENT_CREATED = "client_created", "Client created"
        CLIENT_UPDATED = "client_updated", "Client updated"
        CLIENT_DELETED = "client_deleted", "Client deleted"
        DOCUMENT_UPLOADED = "document_uploaded", "Document uploaded"
        DOCUMENT_VERSION_CREATED = "document_version_created", "Document version created"
        DOCUMENT_OVERWRITTEN = "document_overwritten", "Document overwritten (confirmed)"
        DOCUMENT_VERSION_RESTORED = "document_version_restored", "Older version made current"
        DOCUMENT_DOWNLOADED = "document_downloaded", "Document downloaded"
        DOCUMENT_DELETED = "document_deleted", "Document deleted (recoverable)"
        DOCUMENT_RESTORED = "document_restored", "Document restored"
        DOCUMENT_PURGED = "document_purged", "Document permanently deleted"
        USER_APPROVED = "user_approved", "User approved"
        USER_REJECTED = "user_rejected", "User rejected"
        USER_SUSPENDED = "user_suspended", "User suspended"
        USER_REACTIVATED = "user_reactivated", "User reactivated"
        USER_ROLE_CHANGED = "user_role_changed", "User role changed"
        USER_DELETED = "user_deleted", "User deleted"
        UPLOAD_FAILED = "upload_failed", "File upload failed"
        FILE_INTEGRITY_FAILED = "file_integrity_failed", "File integrity check failed"
        DENIED = "denied", "Action refused"

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user_id_snapshot = models.IntegerField(null=True, blank=True)
    username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=40, choices=Action.choices, db_index=True)
    entity_type = models.CharField(max_length=40)
    entity_id = models.CharField(max_length=64, blank=True)
    entity_label = models.CharField(max_length=255, blank=True)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    description = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    objects = AuditEventQuerySet.as_manager()

    class Meta:
        ordering = ["-timestamp", "-id"]
        indexes = [models.Index(fields=["entity_type", "entity_id"], name="audit_entity_idx")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Audit history is append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit history is append-only and cannot be deleted.")

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.username or 'system'} {self.action} {self.entity_type} {self.entity_label}"
