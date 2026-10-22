import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts import permissions as perm
from audit.models import AuditEvent
from audit.services import log_event

from . import services
from .filters import JobFilter
from .forms import BeaconFormSet, JobForm
from .models import Job

log = logging.getLogger("swiftvertex")
A = AuditEvent.Action
L = Job.Lifecycle
PAGE_SIZE = 50

STALE_MESSAGE = (
    "THIS JOB WAS CHANGED BY SOMEONE ELSE AFTER YOU OPENED IT - YOUR CHANGES WERE NOT SAVED. "
    "Open the job again to see the latest version, then repeat your edit."
)
NOT_SAVED_MESSAGE = "RECORD WAS NOT SAVED - the database refused the change. Nothing was changed."


def _beacons(job):
    return [{k: str(v) for k, v in row.items()}
            for row in job.survey_points.order_by("beacon_number").values("beacon_number", "northing", "easting", "height")]


@login_required
def job_list(request):
    perm.require(perm.can_view_jobs(request.user))
    show = request.GET.get("show", "active")
    if show == "deleted" and perm.can_delete(request.user):
        base = Job.all_objects.filter(lifecycle=L.DELETED)
    elif show == "archived":
        base = Job.objects.filter(lifecycle=L.ARCHIVED)
    elif show == "all":
        base = Job.objects.all()
    else:
        show = "active"
        base = Job.objects.filter(lifecycle=L.ACTIVE)
    base = perm.visible_jobs(request.user, base)
    job_filter = JobFilter(request.GET, queryset=base.select_related("client", "assigned_to"))
    from clients.models import Client
    job_filter.form.fields["client"].queryset = Client.objects.filter(jobs__in=base).distinct()
    page = Paginator(job_filter.qs, PAGE_SIZE).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    visible = perm.visible_jobs(request.user)
    counts = {
        "all": visible.count(),
        "active": visible.filter(lifecycle=L.ACTIVE).count(),
        "archived": visible.filter(lifecycle=L.ARCHIVED).count(),
    }
    if perm.can_delete(request.user):
        counts["deleted"] = perm.visible_jobs(
            request.user, Job.all_objects.filter(lifecycle=L.DELETED)
        ).count()
    return render(request, "jobs/job_list.html", {
        "filter": job_filter, "jobs": page, "page": page, "show": show,
        "query_string": params.urlencode(), "counts": counts,
        "may_export": perm.can_view_reports(request.user),
    })


@login_required
def job_detail(request, pk):
    job = get_object_or_404(Job.all_objects.select_related("client", "assigned_to", "project"), pk=pk)
    if not perm.can_view_job(request.user, job):
        raise Http404
    if job.lifecycle == L.DELETED and not perm.can_delete(request.user):
        raise Http404
    return render(request, "jobs/job_detail.html", {
        "job": job, "survey_points": job.survey_points.all(),
        "documents": job.documents.filter(lifecycle="active"),
    })


@login_required
def job_create(request):
    perm.require(perm.can_create(request.user))
    duplicate = None
    if request.method == "POST":
        batch_import = request.POST.get("post_action") == "batch_import"
        form = JobForm(request.POST, user=request.user)
        formset = BeaconFormSet(request.POST, instance=Job(), prefix="beacons")
        if form.is_valid() and formset.is_valid():
            beacon_forms = [f for f in formset if f.cleaned_data and not f.cleaned_data.get("DELETE")]
            if not beacon_forms and not batch_import:
                messages.error(request, "Add at least one survey beacon before saving the job.")
            else:
                try:
                    with transaction.atomic():
                        job = form.save(commit=False)
                        job.created_by = request.user
                        job.save()
                        formset.instance = job
                        formset.save()
                        log_event(user=request.user, action=A.JOB_CREATED, entity_type="job", entity_id=job.pk,
                                  entity_label=job.plan_number, new={**services.snapshot(job), "beacons": _beacons(job)},
                                  description=f"Created job {job.plan_number}", request=request)
                except IntegrityError:
                    log.exception("Job create failed at the database")
                    if Job.all_objects.filter(plan_number__iexact=form.cleaned_data["plan_number"]).exists():
                        form.duplicate_job = Job.all_objects.filter(plan_number__iexact=form.cleaned_data["plan_number"]).first()
                        form.add_error("plan_number", "JOB NUMBER ALREADY EXISTS")
                        messages.error(request, "JOB NUMBER ALREADY EXISTS - the record was not saved.")
                    else:
                        messages.error(request, NOT_SAVED_MESSAGE)
                else:
                    if batch_import:
                        messages.success(request, f"Job '{job.title}' created. Import its survey beacon coordinates below.")
                        return redirect(
                            f"{reverse('survey_points:create', args=[job.pk])}?mode=batch"
                        )
                    messages.success(request, f"Job '{job.title}' created with {len(beacon_forms)} beacon(s).")
                    return redirect("jobs:detail", pk=job.pk)
        elif "plan_number" in form.errors and form.duplicate_job:
            messages.error(request, "JOB NUMBER ALREADY EXISTS - the record was not saved.")
    else:
        form = JobForm(user=request.user)
        formset = BeaconFormSet(instance=Job(), prefix="beacons")
    return render(request, "jobs/job_form.html", {"form": form, "formset": formset, "title": "New job"})


@login_required
def job_update(request, pk):
    existing = get_object_or_404(Job, pk=pk)
    perm.require(perm.can_edit_job(request.user, existing))
    if existing.lifecycle != L.ACTIVE:
        messages.error(request, "ARCHIVED JOBS ARE READ-ONLY - restore the job before editing it.")
        return redirect("jobs:detail", pk=pk)

    if request.method == "POST":
        saved = False
        try:
            with transaction.atomic():
                job = Job.objects.select_for_update().get(pk=pk)
                before, before_beacons = services.snapshot(job), _beacons(job)
                form = JobForm(request.POST, instance=job, user=request.user)
                formset = BeaconFormSet(request.POST, instance=job, prefix="beacons")
                if form.is_valid() and formset.is_valid():
                    remaining = [f for f in formset if f.cleaned_data and not f.cleaned_data.get("DELETE")]
                    expected = form.cleaned_data.get("expected_version")
                    if expected is None or expected != job.row_version:
                        form.add_error(None, STALE_MESSAGE)
                        messages.error(request, STALE_MESSAGE)
                    elif not remaining:
                        messages.error(request, "A job must have at least one survey beacon.")
                    else:
                        obj = form.save(commit=False)
                        obj.row_version = expected + 1
                        obj.save()
                        formset.save()
                        old, new = services.diff(before, services.snapshot(obj))
                        if old:
                            action = A.JOB_STATUS_CHANGED if set(old) == {"status"} else A.JOB_UPDATED
                            log_event(user=request.user, action=action, entity_type="job", entity_id=obj.pk,
                                      entity_label=obj.plan_number, old=old, new=new,
                                      description=f"Updated job {obj.plan_number}: {', '.join(sorted(old))}", request=request)
                        after_beacons = _beacons(obj)
                        if before_beacons != after_beacons:
                            log_event(user=request.user, action=A.BEACONS_CHANGED, entity_type="job", entity_id=obj.pk,
                                      entity_label=obj.plan_number, old={"beacons": before_beacons},
                                      new={"beacons": after_beacons}, description="Survey beacons changed", request=request)
                        saved = True
        except IntegrityError:
            log.exception("Job update failed at the database")
            messages.error(request, NOT_SAVED_MESSAGE)
            job = get_object_or_404(Job, pk=pk)
            form = JobForm(request.POST, instance=job, user=request.user)
            formset = BeaconFormSet(request.POST, instance=job, prefix="beacons")
        if saved:
            messages.success(request, "Job updated.")
            return redirect("jobs:detail", pk=pk)
    else:
        form = JobForm(instance=existing, user=request.user)
        formset = BeaconFormSet(instance=existing, prefix="beacons")
    return render(request, "jobs/job_form.html", {"form": form, "formset": formset, "title": f"Edit {existing.title}", "job": existing})


def _lifecycle_view(request, pk, *, action, allowed, service, verb, needs_state, danger=False, typed=False):
    job = get_object_or_404(Job.all_objects, pk=pk)
    perm.require(allowed(request.user))
    if job.lifecycle not in needs_state:
        messages.error(request, f"CANNOT {verb.upper()} - this job is {job.get_lifecycle_display().lower()}.")
        return redirect("jobs:detail", pk=pk)
    if request.method == "POST":
        try:
            if typed:
                counts = service(pk, request.user, request.POST.get("confirm_text", ""), request=request)
            else:
                service(pk, request.user, request=request)
        except services.LifecycleError as exc:
            messages.error(request, str(exc))
        else:
            if typed:
                messages.success(request, f"Job {job.plan_number} permanently deleted ({counts[0]} beacon(s), {counts[1]} document(s)).")
                return redirect("jobs:list")
            messages.success(request, f"Job {job.plan_number}: {verb} completed.")
            return redirect("jobs:detail", pk=pk)
    return render(request, "jobs/job_lifecycle_confirm.html", {
        "job": job, "action": action, "verb": verb, "danger": danger, "typed": typed,
        "phrase": f"DELETE {job.plan_number}",
    })


@login_required
def job_archive(request, pk):
    return _lifecycle_view(request, pk, action="archive", allowed=perm.can_archive, service=services.archive_job,
                           verb="archive", needs_state={L.ACTIVE})


@login_required
def job_restore(request, pk):
    return _lifecycle_view(request, pk, action="restore", allowed=perm.can_restore, service=services.restore_job,
                           verb="restore", needs_state={L.ARCHIVED, L.DELETED})


@login_required
def job_delete(request, pk):
    """Soft delete: the job disappears from normal lists but can still be restored."""
    return _lifecycle_view(request, pk, action="delete", allowed=perm.can_delete, service=services.soft_delete_job,
                           verb="delete", needs_state={L.ARCHIVED}, danger=True)


@login_required
def job_purge(request, pk):
    return _lifecycle_view(request, pk, action="purge", allowed=perm.can_delete, service=services.purge_job,
                           verb="permanently delete", needs_state={L.DELETED}, danger=True, typed=True)
