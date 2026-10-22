from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from .models import Profile

SUPER_ADMIN = "super_admin"
R = Profile.Role
FULL_ACCESS = {SUPER_ADMIN, R.ADMIN, R.OFFICE_STAFF}
JOB_CREATORS = {SUPER_ADMIN, R.ADMIN, R.OFFICE_STAFF, R.SURVEYOR}
CAN_DELETE = {SUPER_ADMIN, R.ADMIN}


def role_of(user):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    if user.is_superuser:
        return SUPER_ADMIN
    profile = getattr(user, "profile", None)
    return profile.role if profile else None


def assigned_role(user):
    if user.is_superuser:
        return SUPER_ADMIN
    profile = getattr(user, "profile", None)
    return profile.role if profile else None


def _owns(user, obj):
    return bool(obj) and user.pk in (getattr(obj, "created_by_id", None), getattr(obj, "assigned_to_id", None))


def can_view(user):
    return role_of(user) is not None


def can_view_job(user, job):
    role = role_of(user)
    if role in FULL_ACCESS:
        return True
    if role == R.SURVEYOR:
        return _owns(user, job)
    if role == R.CLIENT_VIEWER:
        profile = getattr(user, "profile", None)
        return bool(profile and profile.client_id and job.client_id == profile.client_id)
    return False


def can_view_jobs(user):
    return role_of(user) in FULL_ACCESS | {R.SURVEYOR, R.CLIENT_VIEWER}


def visible_jobs(user, queryset=None):
    from jobs.models import Job

    jobs = queryset if queryset is not None else Job.objects.all()
    role = role_of(user)
    if role in FULL_ACCESS:
        return jobs
    if role == R.SURVEYOR:
        return jobs.filter(Q(assigned_to=user) | Q(created_by=user))
    if role == R.CLIENT_VIEWER:
        profile = getattr(user, "profile", None)
        return jobs.filter(client_id=profile.client_id) if profile and profile.client_id else jobs.none()
    return jobs.none()


def can_view_client_list(user):
    return role_of(user) in FULL_ACCESS


def can_view_client(user, client):
    role = role_of(user)
    if role in FULL_ACCESS:
        return True
    if role == R.SURVEYOR:
        return client is not None and client.created_by_id == user.pk
    return False


def can_create(user):
    return role_of(user) in JOB_CREATORS


def can_edit_job(user, job):
    role = role_of(user)
    return role in FULL_ACCESS or (role == R.SURVEYOR and _owns(user, job))


def can_edit_content(user, job):
    return can_edit_job(user, job)


def can_edit_client(user, client=None):
    role = role_of(user)
    if role in FULL_ACCESS:
        return True
    return role == R.SURVEYOR and client is not None and client.created_by_id == user.pk


def can_view_reports(user):
    return role_of(user) in FULL_ACCESS


def can_archive(user):
    return role_of(user) in {SUPER_ADMIN, R.ADMIN}


can_restore = can_archive


def can_delete(user):
    return role_of(user) in CAN_DELETE


def can_manage_users(user, target_role=None):
    return bool(getattr(user, "is_superuser", False))


def can_manage_roles(user):
    return bool(getattr(user, "is_superuser", False))


def can_assign_role(actor, target, new_role):
    return (
        can_manage_roles(actor)
        and actor.pk != target.pk
        and new_role in Profile.Role.values
    )


def require(allowed):
    if not allowed:
        raise PermissionDenied


def superuser_required(view_func):
    @login_required
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapped


def role_required(*roles):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            require(role_of(request.user) in roles)
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator
