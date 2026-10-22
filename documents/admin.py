from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "job", "doc_type", "uploaded_by", "uploaded_at")
    list_filter = ("doc_type",)
    search_fields = ("title", "job__title")

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser  # everyone else deletes through the audited application screens
