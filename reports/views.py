from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import render

from accounts import permissions as perm
from jobs.models import Job

from .services import jobs_to_csv, jobs_to_docx


def _selected_jobs(request):
    raw_ids = request.GET.getlist("job_ids")
    if not raw_ids:
        raw_ids = request.GET.get("job_ids", "").split(",")
    values = []
    for value in raw_ids:
        for item in str(value).split():
            item = item.strip()
            if item:
                values.append(item)
    if values:
        cleaned = []
        for item in values:
            if item.isdigit():
                cleaned.append(int(item))
        if cleaned:
            return Job.objects.filter(pk__in=cleaned).select_related("client", "assigned_to").prefetch_related("survey_points")
    return Job.objects.select_related("client", "assigned_to").prefetch_related("survey_points")


@login_required
def reports_home(request):
    perm.require(perm.can_view_reports(request.user))
    status_breakdown = Job.objects.values("status").annotate(total=Count("id")).order_by("status")
    return render(request, "reports/reports.html", {"status_breakdown": status_breakdown})


@login_required
def export_jobs_csv(request):
    perm.require(perm.can_view_reports(request.user))
    return jobs_to_csv(_selected_jobs(request))


@login_required
def export_jobs_docx(request):
    perm.require(perm.can_view_reports(request.user))
    if request.GET.get("selection") == "1" and not request.GET.getlist("job_ids"):
        return HttpResponse("Select at least one job to export.", status=400)
    jobs = _selected_jobs(request)
    if not jobs.exists():
        return HttpResponse("No jobs available for export.", status=400)
    return jobs_to_docx(jobs)
