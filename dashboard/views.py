import json
from datetime import date
from django.utils import timezone

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils.dateparse import parse_date

from accounts import permissions as perm
from accounts.permissions import role_of
from accounts.models import Profile
from clients.models import Client
from documents.models import Document
from jobs.models import Job
from survey_points.models import SurveyPoint

DASHBOARD_CACHE_SECONDS = 60  # 0 = no caching

QUARTER_LABELS = {1: "Q1 (Jan - Mar)", 2: "Q2 (Apr - Jun)", 3: "Q3 (Jul - Sep)", 4: "Q4 (Oct - Dec)"}
QUARTER_SHORT = {1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4"}


def _quarter_of(d):
    return (d.month - 1) // 3 + 1


def _quarter_end(year, quarter):
    """Last calendar day of the given quarter."""
    end_month = quarter * 3
    if end_month == 12:
        return date(year, 12, 31)
    next_month_first = date(year, end_month + 1, 1)
    from datetime import timedelta
    return next_month_first - timedelta(days=1)


def _prev_quarter(year, quarter):
    if quarter == 1:
        return year - 1, 4
    return year, quarter - 1


def _pct_change(current, previous):
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100)


def home(request):
    """Signed-out visitors see the public welcome page; members see the dashboard."""
    if not request.user.is_authenticated:
        from accounts.views import welcome
        return welcome(request)
    return _dashboard(request)


@login_required
def _dashboard(request):
    base_jobs = perm.visible_jobs(request.user)
    today = date.today()

    try:
        selected_quarter = int(request.GET.get("quarter", _quarter_of(today)))
        if selected_quarter not in (1, 2, 3, 4):
            selected_quarter = _quarter_of(today)
    except (TypeError, ValueError):
        selected_quarter = _quarter_of(today)

    try:
        selected_year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        selected_year = today.year

    q_end = _quarter_end(selected_year, selected_quarter)
    prev_year, prev_quarter = _prev_quarter(selected_year, selected_quarter)
    prev_q_end = _quarter_end(prev_year, prev_quarter)

    def stat(current_count, previous_count):
        return {"value": current_count, "change": _pct_change(current_count, previous_count)}

    # --- Client queryset (same permission rules as before) ---
    client_query = Client.objects.all()
    role = role_of(request.user)
    if role == Profile.Role.SURVEYOR:
        client_query = client_query.filter(created_by=request.user)
    elif role == Profile.Role.CLIENT_VIEWER:
        profile = request.user.profile
        client_query = client_query.filter(pk=profile.client_id) if profile.client_id else client_query.none()

    # Year dropdown options: a small window around the data / today.
    years = list(range(today.year - 2, today.year + 2))

    # Quarters for the chart: 5 quarters ending on the selected quarter
    quarters = []
    y, qtr = selected_year, selected_quarter
    for _ in range(5):
        quarters.append((y, qtr))
        y, qtr = _prev_quarter(y, qtr)
    quarters.reverse()

    def build_numbers():
        """Runs only a handful of queries (instead of ~25) and is cached."""
        done = Q(status=Job.Status.COMPLETED)

        # ONE query for every jobs number
        chart_aggs = {}
        for i, (qy, qq) in enumerate(quarters):
            q_start = date(qy, (qq - 1) * 3 + 1, 1)
            chart_aggs[f"chart_{i}"] = Count(
                "id", distinct=True,
                filter=Q(job_date__gte=q_start, job_date__lte=_quarter_end(qy, qq)),
            )
        jobs = base_jobs.aggregate(
            total_now=Count("id", distinct=True, filter=Q(created_at__date__lte=q_end)),
            total_prev=Count("id", distinct=True, filter=Q(created_at__date__lte=prev_q_end)),
            active_now=Count("id", distinct=True, filter=~done & Q(created_at__date__lte=q_end)),
            active_prev=Count("id", distinct=True, filter=~done & Q(created_at__date__lte=prev_q_end)),
            completed_now=Count("id", distinct=True, filter=done & Q(updated_at__date__lte=q_end)),
            completed_prev=Count("id", distinct=True, filter=done & Q(updated_at__date__lte=prev_q_end)),
            **chart_aggs,
        )

        # ONE query for clients
        clients = client_query.aggregate(
            now=Count("id", filter=Q(created_at__date__lte=q_end)),
            prev=Count("id", filter=Q(created_at__date__lte=prev_q_end)),
            total=Count("id"),
        )

        # ONE query for documents
        docs = Document.objects.filter(job__in=base_jobs).aggregate(
            now=Count("id", filter=Q(uploaded_at__date__lte=q_end)),
            prev=Count("id", filter=Q(uploaded_at__date__lte=prev_q_end)),
            total=Count("id"),
        )

        recent = []
        for job in base_jobs.select_related("client").order_by("-created_at")[:5]:
            recent.append({
                "plan_number": job.plan_number,
                "title": job.title,
                "client": job.client.name if job.client else "\u2014",
                "location": job.job_address,
                "date": job.job_date,
                "status": job.status,
                "status_display": job.get_status_display(),
            })

        return {
            "jobs": jobs,
            "clients": clients,
            "docs": docs,
            "recent_jobs": recent,
            "survey_points": SurveyPoint.objects.filter(job__in=base_jobs).count(),
        }

    # Cached per user + selected quarter/year. Numbers can be up to
    # DASHBOARD_CACHE_SECONDS old. Set it to 0 to disable caching.
    cache_key = f"dash:{request.user.pk}:{selected_year}:{selected_quarter}"
    data = cache.get_or_set(cache_key, build_numbers, DASHBOARD_CACHE_SECONDS)
    jobs, clients, docs = data["jobs"], data["clients"], data["docs"]

    chart_labels = [f"{QUARTER_SHORT[qq]} {qy}" for qy, qq in quarters]
    chart_values = [jobs[f"chart_{i}"] for i in range(len(quarters))]
    recent_jobs = data["recent_jobs"]

    hour = timezone.localtime().hour
    if hour < 12:
        greeting_word = "Good morning"
    elif hour < 17:
        greeting_word = "Good afternoon"
    else:
        greeting_word = "Good evening"

    context = {
        "greeting_word": greeting_word,
        "greeting_name": request.user.first_name or request.user.username,
        "selected_quarter": selected_quarter,
        "selected_year": selected_year,
        "quarter_choices": QUARTER_LABELS,
        "year_choices": years,
        "prev_quarter_label": f"{QUARTER_SHORT[prev_quarter]} {prev_year}",

        "total_jobs": stat(jobs["total_now"], jobs["total_prev"]),
        "active_jobs": stat(jobs["active_now"], jobs["active_prev"]),
        "completed_jobs": stat(jobs["completed_now"], jobs["completed_prev"]),
        "total_clients": stat(clients["now"], clients["prev"]),
        "total_documents": stat(docs["now"], docs["prev"]),

        "chart_labels": json.dumps(chart_labels),
        "chart_values": json.dumps(chart_values),

        "recent_jobs": recent_jobs,

        # kept for any other templates/backwards compat
        "active_jobs_count": jobs["active_now"],
        "clients_count": clients["total"],
        "survey_points_count": data["survey_points"],
        "documents_count": docs["total"],
    }
    return render(request, "dashboard/dashboard.html", context)


@login_required
def search(request):
    """Global search across jobs: by title, plan number, address, job
    date, and beacon number (via related survey points)."""
    query = request.GET.get("q", "").strip()
    jobs = Job.objects.none()

    if query:
        filters = (
            Q(title__icontains=query)
            | Q(plan_number__icontains=query)
            | Q(job_address__icontains=query)
            | Q(reference_number__icontains=query)
            | Q(survey_points__beacon_number__icontains=query)
            | Q(client__name__icontains=query)
            | Q(documents__lifecycle="active", documents__versions__original_filename__icontains=query)
            | Q(documents__lifecycle="active", documents__title__icontains=query)
        )
        possible_date = parse_date(query)
        if possible_date:
            filters |= Q(job_date=possible_date)

        jobs = (
            perm.visible_jobs(request.user, Job.objects.select_related("client"))
            .filter(filters)
            .distinct()
            .order_by("-created_at")
        )

    return render(request, "dashboard/search_results.html", {"query": query, "jobs": jobs})



