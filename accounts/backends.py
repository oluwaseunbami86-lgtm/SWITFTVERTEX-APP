from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrUsernameBackend(ModelBackend):
    """Let people sign in with their email address (or their old username)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None
        User = get_user_model()
        qs = User.objects.filter(Q(username__iexact=username) | Q(email__iexact=username))
        users = list(qs[:2])
        if len(users) > 1:
            # Ambiguous email: only accept an exact username match.
            users = [u for u in users if u.username.lower() == username.lower()]
        if not users:
            User().set_password(password)  # equalise timing
            return None
        user = users[0]
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def user_can_authenticate(self, user):
        if not super().user_can_authenticate(user):
            return False
        profile = getattr(user, "profile", None)
        return bool(profile and profile.status == profile.Status.APPROVED)
