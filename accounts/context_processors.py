from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static


def public_assets(request):
    """Optional hero photo and legal links for the public pages."""
    hero_path = "img/hero_photo.png"
    hero = static(hero_path) if finders.find(hero_path) else ""
    return {
        "hero_photo_url": hero,
        "terms_url": getattr(settings, "TERMS_URL", ""),
        "privacy_url": getattr(settings, "PRIVACY_URL", ""),
    }


def super_admin_context(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_superuser:
        return {"pending_approvals_count": 0}

    from .models import Profile

    return {"pending_approvals_count": Profile.objects.filter(status=Profile.Status.PENDING).count()}
