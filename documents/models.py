from django.conf import settings
from django.db import models
from django.db.models import Q

from jobs.models import Job


def job_document_path(instance, filename):
    # Legacy only: documents uploaded before versioning used Django's FileField.
    return f"jobs/{instance.job_id}/documents/{filename}"


class Document(models.Model):
    """One logical file in a job's archive (e.g. 'Survey_Plan.dwg'). Its content
    lives in one or more DocumentVersion rows; exactly one version is current."""

    class DocType(models.TextChoices):
        DRAWING = "drawing", "Drawing/plan (CAD)"
        COORDINATES = "coordinates", "Coordinates"
        PHOTO = "photo", "Photo"
        PDF_REPORT = "pdf_report", "Report / PDF"
        FIELD_DATA = "field_data", "Field data"
        DELIVERABLE = "deliverable", "Deliverable"
        OTHER = "other", "Other"

    class Lifecycle(models.TextChoices):
        ACTIVE = "active", "Active"
        DELETED = "deleted", "Deleted"

    job = models.ForeignKey(Job, on_delete=models.PROTECT, related_name="documents")
    # Legacy single-file field, kept so existing records are never lost. New uploads use versions.
    file = models.FileField(upload_to=job_document_path, blank=True)
    doc_type = models.CharField(max_length=20, choices=DocType.choices, default=DocType.OTHER)
    title = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    lifecycle = models.CharField(max_length=10, choices=Lifecycle.choices, default=Lifecycle.ACTIVE)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="documents_deleted"
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="documents_uploaded"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [models.Index(fields=["job", "lifecycle"], name="doc_job_lifecycle_idx")]
        constraints = [
            models.CheckConstraint(check=Q(lifecycle__in=["active", "deleted"]), name="document_lifecycle_valid"),
        ]

    def __str__(self):
        return self.title or self.filename

    @property
    def current_version(self):
        cached = getattr(self, "_current_cache", None)
        if cached is not None:
            return cached
        return self.versions.filter(is_current=True).first()

    @property
    def filename(self):
        v = self.current_version
        if v:
            return v.original_filename
        return self.file.name.rsplit("/", 1)[-1] if self.file else ""


class DocumentVersion(models.Model):
    class Integrity(models.TextChoices):
        UNVERIFIED = "unverified", "Not yet verified"
        OK = "ok", "Verified"
        MISSING = "missing", "FILE MISSING"
        CORRUPT = "corrupt", "CHECKSUM MISMATCH"

    class Backend(models.TextChoices):
        OBJECT_STORE = "object_store", "Object storage"
        LEGACY_MEDIA = "legacy_media", "Legacy local media (not yet moved to R2)"

    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="versions")
    version_number = models.PositiveIntegerField()
    is_current = models.BooleanField(default=False)

    original_filename = models.CharField(max_length=255)
    extension = models.CharField(max_length=10, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64, blank=True)
    storage_key = models.CharField(max_length=700, unique=True)
    storage_backend = models.CharField(max_length=20, choices=Backend.choices, default=Backend.OBJECT_STORE)

    change_note = models.CharField(max_length=500, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="document_versions_uploaded"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    overwritten_at = models.DateTimeField(null=True, blank=True)
    overwritten_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    integrity_status = models.CharField(max_length=12, choices=Integrity.choices, default=Integrity.UNVERIFIED)
    last_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-version_number"]
        constraints = [
            models.UniqueConstraint(fields=["document", "version_number"], name="uniq_document_version_number"),
            models.UniqueConstraint(fields=["document"], condition=Q(is_current=True), name="uniq_current_version_per_document"),
            models.CheckConstraint(check=Q(version_number__gte=1), name="docversion_number_positive"),
            models.CheckConstraint(check=Q(size_bytes__gte=0), name="docversion_size_non_negative"),
            models.CheckConstraint(
                check=Q(sha256="") | Q(sha256__regex=r"^[0-9a-f]{64}$"), name="docversion_sha256_format"
            ),
        ]

    def __str__(self):
        return f"{self.original_filename} v{self.version_number}"


class ReplacedFile(models.Model):
    """The previous content of a version that was deliberately overwritten. The
    old object is kept until `purge_after` so an accidental overwrite can be undone."""

    version = models.ForeignKey(DocumentVersion, on_delete=models.PROTECT, related_name="replaced_files")
    storage_key = models.CharField(max_length=700, unique=True)
    original_filename = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64, blank=True)
    replaced_at = models.DateTimeField(auto_now_add=True)
    replaced_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    purge_after = models.DateTimeField()
    purged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-replaced_at"]
