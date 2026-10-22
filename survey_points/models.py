from django.conf import settings
from django.db import models

from jobs.models import Job


class SurveyPoint(models.Model):
    """A single survey beacon captured for a job.

    One Job -> many SurveyPoint records (one-to-many). Kept as individual
    rows rather than packed into one field so each beacon is queryable,
    exportable, and ready for future GIS/PostGIS work.
    """

    job = models.ForeignKey(Job, on_delete=models.PROTECT, related_name="survey_points")
    beacon_number = models.CharField(
        max_length=100, db_index=True,
        help_text="e.g. BP1234X - editable, does not have to follow the suggested B1/B2 pattern",
    )
    northing = models.DecimalField(max_digits=14, decimal_places=4)
    easting = models.DecimalField(max_digits=14, decimal_places=4)
    height = models.DecimalField(max_digits=10, decimal_places=4)
    notes = models.TextField(blank=True)
    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="survey_points_captured"
    )
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["job", "beacon_number"]

    def __str__(self):
        return f"{self.beacon_number} ({self.job.title})"
