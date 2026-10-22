from django.contrib import admin

from .models import SurveyPoint


@admin.register(SurveyPoint)
class SurveyPointAdmin(admin.ModelAdmin):
    list_display = ("beacon_number", "job", "northing", "easting", "height", "captured_by", "captured_at")
    list_filter = ("job",)
    search_fields = ("beacon_number", "job__title", "job__plan_number")

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser  # everyone else deletes through the audited application screens
