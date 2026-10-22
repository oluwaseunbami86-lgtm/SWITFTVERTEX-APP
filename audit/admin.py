from django.contrib import admin

from .models import AuditEvent, AuditLog


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditEvent)
class AuditEventAdmin(ReadOnlyAdmin):
    list_display = ("timestamp", "username", "action", "entity_type", "entity_label", "description")
    list_filter = ("action", "entity_type")
    search_fields = ("username", "entity_label", "entity_id", "description")
    date_hierarchy = "timestamp"


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ("timestamp", "user", "method", "path")
    list_filter = ("method",)
    search_fields = ("path", "user__username")
