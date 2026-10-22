from django.contrib import messages
from django.contrib.auth import SESSION_KEY, get_user_model, logout
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .models import Profile


class ApprovedAccountMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            if request.path.startswith("/admin/") and not user.is_superuser:
                raise PermissionDenied
            profile = getattr(user, "profile", None)
            if user.is_active and profile and profile.status == Profile.Status.APPROVED:
                return self.get_response(request)
        else:
            user_id = request.session.get(SESSION_KEY)
            if user_id:
                user = get_user_model().objects.filter(pk=user_id).first()
                if user is None:
                    logout(request)
                    return redirect("login")

        if user is not None and getattr(user, "pk", None):
            profile = getattr(user, "profile", None)
            status = profile.status if profile else Profile.Status.PENDING
            if user.is_active and status == Profile.Status.APPROVED:
                return self.get_response(request)
            if status == Profile.Status.REJECTED:
                message = "Your account has been rejected."
                if profile.rejection_reason:
                    message = f"{message} Reason: {profile.rejection_reason}"
            elif status == Profile.Status.SUSPENDED or not user.is_active:
                message = "Your account has been suspended. Contact the administrator."
            else:
                message = "Your account is awaiting approval by the administrator."
            logout(request)
            messages.error(request, message)
            return redirect("login")

        return self.get_response(request)
