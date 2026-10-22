from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Upper
from django.urls import reverse

from clients.models import Client

# OD/1379/2026/041  ->  <letters>/<number>/<4-digit year>/<3+ digit sequence>
PLAN_NUMBER_REGEX = r"^[A-Z]{1,10}/[0-9]+/[0-9]{4}/[0-9]{3,}$"
plan_number_validator = RegexValidator(
    PLAN_NUMBER_REGEX,
    "PLAN NUMBER FORMAT IS NOT VALID - use the form OD/1379/2026/041 "
    "(letters / number / year / three-digit sequence).",
)


class Project(models.Model):
    """A client engagement that can contain several jobs."""

    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="projects")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="projects_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["client__name", "name"]
        constraints = [models.UniqueConstraint(fields=["client", "name"], name="uniq_project_name_per_client")]

    def __str__(self):
        return f"{self.name} ({self.client.name})"


class JobQuerySet(models.QuerySet):
    def visible(self):
        """Everything except soft-deleted jobs."""
        return self.exclude(lifecycle=Job.Lifecycle.DELETED)


class VisibleJobManager(models.Manager.from_queryset(JobQuerySet)):
    """Default manager: soft-deleted jobs never appear in normal queries,
    lists, search, dashboard or related lookups. Use Job.all_objects to reach them."""

    def get_queryset(self):
        return super().get_queryset().exclude(lifecycle=Job.Lifecycle.DELETED)


class Job(models.Model):
    class Status(models.TextChoices):
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        ON_HOLD = "on_hold", "On hold"

    class SurveyType(models.TextChoices):
        BOUNDARY = "boundary", "Boundary survey"
        TOPOGRAPHIC = "topographic", "Topographic survey"
        LAYOUT = "layout", "Layout/subdivision"
        ENGINEERING = "engineering", "Engineering survey"
        OTHER = "other", "Other"

    class JobType(models.TextChoices):
        FRESH_JOB = "fresh_job", "Fresh job"
        UPDATING_SURVEY = "updating_survey", "Updating survey"
        SUPERSEDED = "superseded", "Superceded"
        CHANGE_OF_TITLE = "change_of_title", "Change of title"
        AMENDMENT = "amendment", "Amendment"
        OTHER = "other", "Other"
        BOUNDARY = "boundary", "Boundary survey (legacy)"
        TOPOGRAPHIC = "topographic", "Topographic survey (legacy)"
        LAYOUT = "layout", "Layout/subdivision (legacy)"
        ENGINEERING = "engineering", "Engineering survey (legacy)"

    class AreaUnit(models.TextChoices):
        SQM = "sqm", "m\u00b2"
        HECTARES = "hectares", "Hectares"

    # Job information
    survey_type = models.CharField(max_length=20, choices=SurveyType.choices, default=SurveyType.BOUNDARY)
    job_type = models.CharField(max_length=20, choices=JobType.choices, default=JobType.BOUNDARY)
    job_date = models.DateField(help_text="Date of the job")
    title = models.CharField(max_length=255, verbose_name="Job title")
    client = models.ForeignKey(
        Client, on_delete=models.PROTECT, null=True, blank=True, related_name="jobs",
        help_text="Optional",
    )
    job_address = models.CharField(max_length=255, verbose_name="Job address")

    # Survey area
    survey_area = models.DecimalField(max_digits=12, decimal_places=3)
    area_unit = models.CharField(max_length=10, choices=AreaUnit.choices, default=AreaUnit.SQM)

    # Plan number - the permanent, unique identity of the job
    plan_number = models.CharField(
        max_length=100, unique=True, validators=[plan_number_validator],
        help_text="e.g. OD/1379/2026/041",
    )
    legacy_plan_number = models.CharField(
        max_length=100, blank=True, editable=False,
        help_text="The plan number as originally typed, if it was normalised during migration.",
    )

    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, null=True, blank=True, related_name="jobs",
    )

    # Reference number
    has_reference_number = models.BooleanField(default=False)
    reference_number = models.CharField(max_length=100, blank=True)

    # Workflow fields (not part of the required "new job" fields, but kept for job management)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ASSIGNED)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs_assigned"
    )
    description = models.TextField(blank=True)

    # Record lifecycle: ACTIVE -> ARCHIVED -> DELETED (soft) -> permanently deleted (admin only)
    class Lifecycle(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"
        DELETED = "deleted", "Deleted"

    lifecycle = models.CharField(max_length=10, choices=Lifecycle.choices, default=Lifecycle.ACTIVE)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs_archived"
    )
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs_deleted"
    )

    # Optimistic locking: bumped on every edit; an edit based on an older
    # version is refused instead of overwriting someone else's change.
    row_version = models.PositiveIntegerField(default=1)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="jobs_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = VisibleJobManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["job_date"], name="job_date_idx"),
            models.Index(fields=["status"], name="job_status_idx"),
            models.Index(fields=["lifecycle"], name="job_lifecycle_idx"),
            models.Index(fields=["created_at"], name="job_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(check=Q(plan_number__regex=PLAN_NUMBER_REGEX), name="job_plan_number_format"),
            models.CheckConstraint(check=Q(plan_number=Upper("plan_number")), name="job_plan_number_uppercase"),
            models.CheckConstraint(check=Q(survey_area__gt=0), name="job_survey_area_positive"),
            models.CheckConstraint(
                check=Q(lifecycle__in=["active", "archived", "deleted"]), name="job_lifecycle_valid"
            ),
            models.CheckConstraint(
                check=Q(status__in=["assigned", "in_progress", "completed", "on_hold"]), name="job_status_valid"
            ),
        ]

    def save(self, *args, **kwargs):
        if self.plan_number:
            self.plan_number = self.plan_number.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("jobs:detail", kwargs={"pk": self.pk})

    @property
    def status_color(self):
        return {
            self.Status.ASSIGNED: "accent",
            self.Status.IN_PROGRESS: "warning",
            self.Status.COMPLETED: "success",
            self.Status.ON_HOLD: "danger",
        }.get(self.status, "accent")
