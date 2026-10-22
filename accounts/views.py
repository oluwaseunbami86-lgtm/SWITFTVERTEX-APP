import logging

from botocore.exceptions import BotoCoreError
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.views import (
    LoginView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import log_event

from .forms import (
    AccountPasswordChangeForm,
    AdminSetPasswordForm,
    ApprovalForm,
    ProfileForm,
    RejectionForm,
    RoleChangeForm,
    SignupForm,
)
from .models import Profile
from .permissions import superuser_required

User = get_user_model()
A = AuditEvent.Action
logger = logging.getLogger(__name__)


def welcome(request):
    return render(request, "auth/welcome.html")


def signup(request):
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid() and form.cleaned_data.get("website"):
            form.add_error("website", "Unable to submit this request.")
        if form.is_valid():
            full_name = form.cleaned_data["full_name"].strip()
            first_name, _, last_name = full_name.partition(" ")
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=form.cleaned_data["username"],
                        email=form.cleaned_data["email"],
                        password=form.cleaned_data["password1"],
                        first_name=first_name,
                        last_name=last_name,
                    )
                    profile = user.profile
                    profile.role = Profile.Role.CLIENT_VIEWER
                    profile.phone_number = form.cleaned_data["phone_number"]
                    profile.status = Profile.Status.PENDING
                    profile.status_changed_at = timezone.now()
                    profile.save()
            except IntegrityError:
                form.add_error(None, "That username or email address is already registered.")
            else:
                return redirect("signup_pending")
    else:
        form = SignupForm()

    return render(request, "auth/signup.html", {
        "form": form,
        "terms_url": getattr(settings, "TERMS_URL", ""),
        "privacy_url": getattr(settings, "PRIVACY_URL", ""),
    })


def signup_pending(request):
    return render(request, "auth/signup_pending.html")


class ApprovalAuthenticationForm(AuthenticationForm):
    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if not username or not password:
            return super().clean()

        self.user_cache = authenticate(self.request, username=username, password=password)
        if self.user_cache is None:
            matches = list(
                User.objects.filter(Q(username__iexact=username) | Q(email__iexact=username))[:2]
            )
            if len(matches) > 1:
                matches = [u for u in matches if u.username.casefold() == username.casefold()]
            if matches and matches[0].check_password(password):
                profile = getattr(matches[0], "profile", None)
                status = profile.status if profile else Profile.Status.PENDING
                if status == Profile.Status.PENDING:
                    raise ValidationError("Your account is awaiting approval by the administrator.")
                if status == Profile.Status.REJECTED:
                    reason = profile.rejection_reason
                    text = "Your account has been rejected."
                    if reason:
                        text = f"{text} Reason: {reason}"
                    raise ValidationError(text)
                if status == Profile.Status.SUSPENDED:
                    raise ValidationError("Your account has been suspended. Contact the administrator.")
            raise self.get_invalid_login_error()

        self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data


class SVLoginView(LoginView):
    template_name = "registration/login.html"
    authentication_form = ApprovalAuthenticationForm
    redirect_authenticated_user = True


class SVPasswordResetView(PasswordResetView):
    template_name = "registration/password_reset_form.html"
    email_template_name = "registration/password_reset_email.txt"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")


class SVPasswordResetDoneView(PasswordResetDoneView):
    template_name = "registration/password_reset_done.html"


class SVPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "registration/password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")


class SVPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "registration/password_reset_complete.html"


@superuser_required
def pending_approvals(request):
    from clients.models import Client

    users = User.objects.filter(profile__status=Profile.Status.PENDING).select_related("profile").order_by("date_joined")
    return render(request, "accounts/pending_approvals.html", {
        "pending_users": users,
        "role_choices": Profile.Role.choices,
        "all_clients": Client.objects.order_by("name"),
    })


@superuser_required
def approval_action(request, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    target = get_object_or_404(User.objects.select_related("profile"), pk=user_id)
    profile = target.profile
    if profile.status != Profile.Status.PENDING:
        messages.error(request, "This account is no longer pending approval.")
        return redirect("accounts:pending_approvals")

    action = request.POST.get("action")
    if action == "approve":
        form = ApprovalForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Choose a valid role and client before approving this account.")
            return redirect("accounts:pending_approvals")
        old_role = profile.role
        now = timezone.now()
        with transaction.atomic():
            profile.role = form.cleaned_data["role"]
            profile.client = form.cleaned_data["client"] if profile.role == Profile.Role.CLIENT_VIEWER else None
            profile.status = Profile.Status.APPROVED
            profile.approved_by = request.user
            profile.approved_at = now
            profile.status_changed_at = now
            profile.rejection_reason = ""
            profile.save()
            if not target.is_active:
                target.is_active = True
                target.save(update_fields=["is_active"])
            log_event(
                user=request.user, action=A.USER_APPROVED, entity_type="user", entity_id=target.pk,
                entity_label=target.username, old={"status": "pending", "role": old_role},
                new={"status": "approved", "role": profile.role},
                description=f"Approved user {target.username}", request=request,
            )
        _notify_status(target, "approved", "")
        messages.success(request, f"{target.username} was approved.")
    elif action == "reject":
        form = RejectionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "The rejection form was invalid.")
            return redirect("accounts:pending_approvals")
        reason = form.cleaned_data["rejection_reason"].strip()
        with transaction.atomic():
            profile.status = Profile.Status.REJECTED
            profile.status_changed_at = timezone.now()
            profile.rejection_reason = reason
            profile.approved_by = None
            profile.approved_at = None
            profile.save()
            log_event(
                user=request.user, action=A.USER_REJECTED, entity_type="user", entity_id=target.pk,
                entity_label=target.username, old={"status": "pending"},
                new={"status": "rejected"}, description=f"Rejected user {target.username}", request=request,
            )
        _notify_status(target, "rejected", reason)
        messages.success(request, f"{target.username} was rejected.")
    else:
        messages.error(request, "Choose approve or reject.")
    return redirect("accounts:pending_approvals")


@superuser_required
def users_list(request):
    from clients.models import Client

    users = User.objects.select_related("profile", "profile__approved_by", "profile__client").order_by("username")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    role = request.GET.get("role", "")
    account_type = request.GET.get("type", "")
    segment = request.GET.get("segment", "")
    totals = User.objects.aggregate(
        all=Count("pk"),
        admins=Count("pk", filter=Q(is_superuser=False, profile__role=Profile.Role.ADMIN)),
        clients=Count("pk", filter=Q(is_superuser=False, profile__role=Profile.Role.CLIENT_VIEWER)),
        pending=Count("pk", filter=Q(profile__status=Profile.Status.PENDING)),
    )
    if query:
        users = users.filter(
            Q(username__icontains=query) | Q(first_name__icontains=query)
            | Q(last_name__icontains=query) | Q(email__icontains=query)
        )
    if status in Profile.Status.values:
        users = users.filter(profile__status=status)
    else:
        status = ""
    if role == "super_admin":
        users = users.filter(is_superuser=True)
    elif role in Profile.Role.values:
        users = users.filter(is_superuser=False, profile__role=role)
    else:
        role = ""
    if account_type not in {"super_admin", "standard"}:
        account_type = ""
    elif account_type == "super_admin":
        users = users.filter(is_superuser=True)
    else:
        users = users.filter(is_superuser=False)
    segments = {
        "admins": Q(is_superuser=False, profile__role=Profile.Role.ADMIN),
        "clients": Q(is_superuser=False, profile__role=Profile.Role.CLIENT_VIEWER),
        "pending": Q(profile__status=Profile.Status.PENDING),
    }
    if segment in segments:
        users = users.filter(segments[segment])
    else:
        segment = ""
    selected_id = request.GET.get("selected", "")
    selected_user = users.filter(pk=selected_id).first() if selected_id.isdecimal() else None
    return render(request, "accounts/users.html", {
        "users": users,
        "query": query,
        "selected_status": status,
        "selected_role": role,
        "selected_type": account_type,
        "selected_segment": segment,
        "selected_user": selected_user,
        "counts": totals,
        "status_choices": Profile.Status.choices,
        "role_choices": [("super_admin", "Super Admin"), *Profile.Role.choices],
        "all_clients": Client.objects.order_by("name"),
    })


def _users_detail_redirect(user_id):
    return redirect(f"{reverse('accounts:users')}?selected={user_id}")


@superuser_required
def user_status_action(request, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    target = get_object_or_404(User.objects.select_related("profile"), pk=user_id)
    profile = target.profile
    action = request.POST.get("action")
    if target.pk == request.user.pk:
        raise PermissionDenied
    if action == "suspend" and profile.status == Profile.Status.APPROVED:
        if target.is_superuser and User.objects.filter(
            is_superuser=True, is_active=True, profile__status=Profile.Status.APPROVED
        ).count() <= 1:
            messages.error(request, "The last active Super Admin cannot be suspended.")
            return _users_detail_redirect(target.pk)
        new_status, event = Profile.Status.SUSPENDED, A.USER_SUSPENDED
    elif action == "reactivate" and profile.status == Profile.Status.SUSPENDED:
        new_status, event = Profile.Status.APPROVED, A.USER_REACTIVATED
    else:
        messages.error(request, "This status action is not available for the selected account.")
        return _users_detail_redirect(target.pk)

    old_status = profile.status
    now = timezone.now()
    with transaction.atomic():
        profile.status = new_status
        profile.status_changed_at = now
        if new_status == Profile.Status.APPROVED:
            profile.approved_by = request.user
            profile.approved_at = now
            profile.rejection_reason = ""
            target.is_active = True
            target.save(update_fields=["is_active"])
        profile.save()
        log_event(
            user=request.user, action=event, entity_type="user", entity_id=target.pk,
            entity_label=target.username, old={"status": old_status}, new={"status": new_status},
            description=f"{new_status.title()} user {target.username}", request=request,
        )
    _notify_status(target, new_status, "")
    messages.success(request, f"{target.username} is now {profile.get_status_display().lower()}.")
    return _users_detail_redirect(target.pk)


@superuser_required
def user_role_action(request, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    target = get_object_or_404(User.objects.select_related("profile"), pk=user_id)
    if target.pk == request.user.pk:
        raise PermissionDenied
    form = RoleChangeForm(request.POST)
    new_client = form.cleaned_data.get("client") if form.is_valid() else None
    if form.is_valid() and (
        target.profile.role != form.cleaned_data["role"]
        or target.profile.client_id != getattr(new_client, "pk", None)
    ):
        old_role = target.profile.role
        old_client_id = target.profile.client_id
        new_role = form.cleaned_data["role"]
        with transaction.atomic():
            target.profile.role = new_role
            target.profile.client = (
                form.cleaned_data["client"] if new_role == Profile.Role.CLIENT_VIEWER else None
            )
            target.profile.save()
            log_event(
                user=request.user, action=A.USER_ROLE_CHANGED, entity_type="user", entity_id=target.pk,
                entity_label=target.username,
                old={"role": old_role, "client_id": old_client_id},
                new={"role": new_role, "client_id": target.profile.client_id},
                description=f"Changed role for {target.username}", request=request,
            )
        messages.success(request, f"Updated {target.username}'s role.")
    else:
        messages.error(request, "Select a valid role.")
    return _users_detail_redirect(target.pk)


@superuser_required
def user_password_action(request, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    target = get_object_or_404(User, pk=user_id)
    form = AdminSetPasswordForm(target, request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, f"Password reset for {target.username}.")
    else:
        messages.error(request, "The password does not meet the requirements or the confirmation does not match.")
    return _users_detail_redirect(target.pk)


@superuser_required
def user_delete_action(request, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    target = get_object_or_404(User, pk=user_id)
    if target.pk == request.user.pk:
        raise PermissionDenied
    if (target.is_superuser and target.is_active and target.profile.status == Profile.Status.APPROVED
            and User.objects.filter(
        is_superuser=True, is_active=True, profile__status=Profile.Status.APPROVED
    ).count() <= 1):
        messages.error(request, "The last active Super Admin cannot be deleted.")
        return _users_detail_redirect(target.pk)
    username = target.username
    with transaction.atomic():
        log_event(
            user=request.user, action=A.USER_DELETED, entity_type="user", entity_id=target.pk,
            entity_label=username, old={"status": getattr(target.profile, "status", None)},
            description=f"Deleted user {username}", request=request,
        )
        target.delete()
    messages.success(request, f"{username} was deleted.")
    return redirect("accounts:users")


@superuser_required
def audit_log(request):
    events = AuditEvent.objects.all()
    return render(request, "accounts/audit_log.html", {"events": events[:200]})


def _notify_status(user, status, reason):
    if not getattr(settings, "ACCOUNT_STATUS_EMAILS", False) or not user.email:
        return
    body = f"Your SwiftVertex account status is now {status}."
    if status == "rejected" and reason:
        body = f"{body}\n\nReason: {reason}"
    send_mail(
        subject="SwiftVertex account status",
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )


@superuser_required
def team_list(request):
    return redirect("accounts:users")


@login_required
def my_profile(request):
    profile = request.user.profile
    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=profile, user=request.user)
        if form.is_valid():
            old_picture_name = profile.profile_picture.name if profile.profile_picture else ""
            old_picture_storage = profile.profile_picture.storage if old_picture_name else None
            try:
                with transaction.atomic():
                    profile = form.save()
            except (OSError, BotoCoreError):
                logger.exception("Profile storage operation failed for user %s", request.user.pk)
                form.add_error(None, "The profile picture could not be stored. Your changes were not saved.")
            else:
                if old_picture_name and old_picture_name != profile.profile_picture.name:
                    try:
                        old_picture_storage.delete(old_picture_name)
                    except (OSError, BotoCoreError):
                        logger.exception("Could not remove old profile picture for user %s", request.user.pk)
                        messages.error(request, "Profile saved, but the previous picture could not be removed.")
                messages.success(request, "Profile updated.")
                return redirect("accounts:my_profile")
    else:
        form = ProfileForm(instance=profile, user=request.user)
    return render(request, "accounts/profile.html", {"form": form, "section": "profile"})


@login_required
def change_password(request):
    if request.method == "POST":
        form = AccountPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Your password has been changed.")
            return redirect("accounts:change_password")
    else:
        form = AccountPasswordChangeForm(request.user)
    return render(request, "accounts/profile.html", {"form": form, "section": "password"})


@login_required
def delete_account_info(request):
    return render(request, "accounts/profile.html", {"section": "delete"})


def permission_denied(request, exception=None):
    return render(request, "403.html", status=403)
