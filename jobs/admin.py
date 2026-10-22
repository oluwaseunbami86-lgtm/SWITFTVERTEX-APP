from django.contrib import admin

from .models import Job, Project


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("title", "plan_number", "job_date", "client", "survey_type", "job_type", "status", "lifecycle", "assigned_to")
    list_filter = ("status", "lifecycle", "survey_type", "job_type", "area_unit")
    search_fields = ("title", "plan_number", "job_address", "client__name", "reference_number")
    autocomplete_fields = ["client"]
    readonly_fields = ("lifecycle", "archived_at", "archived_by", "deleted_at", "deleted_by", "row_version",
                       "legacy_plan_number", "created_at", "updated_at")

    def get_queryset(self, request):
        return Job.all_objects.all()

    def has_delete_permission(self, request, obj=None):
        # Jobs are archived / deleted / purged from the application, where the
        # permission checks, typed confirmation and audit trail apply.
        return False


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "client", "created_at")
    search_fields = ("name", "client__name")
    autocomplete_fields = ["client"]
