from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_event

from .models import Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "status", "phone_number", "created_at")
    list_filter = ("role", "status")
    search_fields = ("user__username", "user__email", "phone_number")

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


class ManagedUserAdmin(UserAdmin):
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_superuser or (obj and obj.pk == request.user.pk):
            return False
        if obj and obj.is_superuser and obj.is_active and obj.profile.status == Profile.Status.APPROVED:
            return User.objects.filter(
                is_superuser=True, is_active=True, profile__status=Profile.Status.APPROVED
            ).count() > 1
        return True

    def get_queryset(self, request):
        return super().get_queryset(request) if request.user.is_superuser else User.objects.none()

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        filtered = []
        for title, options in fieldsets:
            fields = tuple(field for field in options.get("fields", ()) if field != "is_active")
            filtered.append((title, {**options, "fields": fields}))
        return tuple(filtered)

    def save_model(self, request, obj, form, change):
        original = User.objects.get(pk=obj.pk) if change else None
        if original and original.pk == request.user.pk and original.is_superuser and not obj.is_superuser:
            raise PermissionDenied("A Super Admin cannot demote their own account.")
        if (original and original.is_superuser and original.is_active
                and original.profile.status == Profile.Status.APPROVED and not obj.is_superuser):
            if User.objects.filter(
                is_superuser=True, is_active=True, profile__status=Profile.Status.APPROVED
            ).count() <= 1:
                raise PermissionDenied("The last active Super Admin cannot be demoted.")
        if original:
            obj.is_active = original.is_active
        with transaction.atomic():
            super().save_model(request, obj, form, change)
            if original and original.is_superuser != obj.is_superuser:
                if obj.is_superuser:
                    profile = obj.profile
                    old_status = profile.status
                    profile.status = Profile.Status.APPROVED
                    profile.approved_by = request.user
                    profile.approved_at = timezone.now()
                    profile.status_changed_at = profile.approved_at
                    profile.rejection_reason = ""
                    profile.save()
                    if old_status != Profile.Status.APPROVED:
                        log_event(
                            user=request.user,
                            action=AuditEvent.Action.USER_APPROVED,
                            entity_type="user",
                            entity_id=obj.pk,
                            entity_label=obj.username,
                            old={"status": old_status},
                            new={"status": Profile.Status.APPROVED},
                            description=f"Approved promoted Super Admin {obj.username}",
                            request=request,
                        )
                log_event(
                    user=request.user,
                    action=AuditEvent.Action.USER_ROLE_CHANGED,
                    entity_type="user",
                    entity_id=obj.pk,
                    entity_label=obj.username,
                    old={"is_superuser": original.is_superuser},
                    new={"is_superuser": obj.is_superuser},
                    description=f"Changed Super Admin status for {obj.username}",
                    request=request,
                )

    def delete_model(self, request, obj):
        self._audit_and_delete(request, [obj])

    def delete_queryset(self, request, queryset):
        self._audit_and_delete(request, list(queryset))

    def _audit_and_delete(self, request, users):
        if any(user.pk == request.user.pk for user in users):
            raise PermissionDenied("A Super Admin cannot delete their own account.")
        active_super_admins = User.objects.filter(
            is_superuser=True, is_active=True, profile__status=Profile.Status.APPROVED
        ).count()
        active_selected = sum(
            user.is_superuser and user.is_active
            and getattr(getattr(user, "profile", None), "status", None) == Profile.Status.APPROVED
            for user in users
        )
        if active_super_admins - active_selected < 1:
            raise PermissionDenied("The last active Super Admin cannot be deleted.")
        with transaction.atomic():
            for user in users:
                log_event(
                    user=request.user,
                    action=AuditEvent.Action.USER_DELETED,
                    entity_type="user",
                    entity_id=user.pk,
                    entity_label=user.username,
                    old={"status": getattr(getattr(user, "profile", None), "status", None)},
                    description=f"Deleted user {user.username}",
                    request=request,
                )
            for user in users:
                user.delete()


admin.site.unregister(User)
admin.site.register(User, ManagedUserAdmin)
