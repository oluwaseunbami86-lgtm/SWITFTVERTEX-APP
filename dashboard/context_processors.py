from django.core.cache import cache

from jobs.models import Job
from accounts.permissions import visible_jobs


def notifications(request):
    """Makes the topbar notification badge count available on every page.

    Cached for 60 seconds per user so it does not hit the database on every page.
    """
    if not request.user.is_authenticated:
        return {}
    key = f"notif:{request.user.pk}"
    count = cache.get(key)
    if count is None:
        count = visible_jobs(request.user).filter(status=Job.Status.ASSIGNED).count()
        cache.set(key, count, 60)
    return {"notifications_count": count}
