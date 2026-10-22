from jobs.models import Job
from accounts.permissions import visible_jobs


def notifications(request):
    """Makes the topbar notification badge count available on every page."""
    if not request.user.is_authenticated:
        return {}
    count = visible_jobs(request.user).filter(status=Job.Status.ASSIGNED).count()
    return {"notifications_count": count}
