import csv
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from accounts import permissions as perm
from jobs.models import Job

from .forms import ManualSurveyPointFormSet, SurveyPointForm
from .services import (
    IMPORT_FIELDS,
    MAX_IMPORT_BYTES,
    MAX_IMPORT_RECORDS,
    MAX_MANUAL_RECORDS,
    build_import_records,
    parse_coordinate_file,
    save_survey_point_records,
    suggest_header_mapping,
    validate_survey_point_records,
)


def _authorized_job(request, job_pk):
    job = get_object_or_404(Job, pk=job_pk)
    perm.require(perm.can_edit_content(request.user, job) and job.lifecycle == Job.Lifecycle.ACTIVE)
    return job


def _render_entry(request, job, **context):
    defaults = {
        "job": job,
        "manual_formset": ManualSurveyPointFormSet(prefix="points"),
        "phase": "entry",
        "import_fields": IMPORT_FIELDS,
        "max_import_bytes": MAX_IMPORT_BYTES,
        "max_import_records": MAX_IMPORT_RECORDS,
        "max_manual_records": MAX_MANUAL_RECORDS,
        "import_errors": [],
        "suggested_mapping": {},
        "headers": [],
    }
    defaults.update(context)
    if defaults.get("upload_error"):
        defaults["selected_mode"] = "batch"
    else:
        defaults.setdefault("selected_mode", "manual")
    return render(request, "survey_points/survey_point_form.html", defaults)


def _mapping_preview(stage):
    return {
        "rows": stage["rows"][:6],
        "vertical_lines": stage["vertical_lines"][:20],
        "column_count": max((len(row) for row in stage["rows"]), default=0),
        "row_count": len(stage["rows"]),
    }


@login_required
def survey_point_create(request, job_pk):
    job = _authorized_job(request, job_pk)
    stage_key = f"survey-point-import-{job.pk}"
    configuration_key = f"{stage_key}-configuration"
    previewed_key = f"{stage_key}-previewed"
    if request.method == "POST":
        action = request.POST.get("action")
        if action in {"manual", None}:
            if action is None and "points-TOTAL_FORMS" not in request.POST:
                legacy_form = SurveyPointForm(request.POST, initial={"job": job})
                if legacy_form.is_valid():
                    record = {
                        field: legacy_form.cleaned_data[field]
                        for field in ("beacon_number", "easting", "northing", "height")
                    }
                    points, issues = save_survey_point_records(
                        job=job, user=request.user, rows=[record], request=request
                    )
                    if not issues:
                        messages.success(request, f"Beacon '{points[0].beacon_number}' recorded.")
                        return redirect("jobs:detail", pk=job.pk)
                else:
                    messages.error(request, "Check the survey point fields and try again.")
                return _render_entry(request, job, legacy_form=legacy_form)
            formset = ManualSurveyPointFormSet(request.POST, prefix="points")
            if formset.is_valid():
                records = [
                    form.cleaned_data
                    for form in formset
                    if form.cleaned_data and not form.cleaned_data.get("DELETE")
                ]
                points, issues = save_survey_point_records(
                    job=job, user=request.user, rows=records, request=request
                )
                if not issues:
                    messages.success(request, f"{len(points)} survey point(s) recorded.")
                    return redirect("jobs:detail", pk=job.pk)
                return _render_entry(
                    request, job, manual_formset=formset, manual_errors=issues,
                )
            return _render_entry(request, job, manual_formset=formset)

        if action == "upload":
            uploaded = request.FILES.get("coordinate_file")
            if not uploaded:
                return _render_entry(request, job, upload_error="Select a CSV or TXT file.")
            if not uploaded.name.lower().endswith((".csv", ".txt")):
                return _render_entry(request, job, upload_error="Only CSV and TXT files are supported.")
            try:
                stage = parse_coordinate_file(uploaded)
            except (ValueError, UnicodeError, csv.Error) as exc:
                return _render_entry(request, job, upload_error=str(exc))
            request.session[stage_key] = stage
            request.session.pop(configuration_key, None)
            request.session.pop(previewed_key, None)
            headers = stage["rows"][0] if stage["mode_guess"] == "columns" else []
            suggestions = suggest_header_mapping(headers)
            likely_header = len(suggestions) >= 2
            return _render_entry(
                request,
                job,
                phase="mapping",
                stage=stage,
                mapping_preview=_mapping_preview(stage),
                detected_row_count=len(stage["rows"]),
                headers=headers,
                suggested_mapping=suggestions,
                likely_header=likely_header,
            )

        if action == "preview":
            stage = request.session.get(stage_key)
            if not stage:
                return _render_entry(request, job, upload_error="Your staged file has expired. Select it again.")
            try:
                configuration = json.loads(request.POST.get("configuration", "{}"))
                if not isinstance(configuration, dict):
                    raise ValueError
            except (TypeError, ValueError, json.JSONDecodeError):
                return _render_entry(request, job, upload_error="The field mapping was invalid. Select the file again.")
            records, structural_issues = build_import_records(stage, configuration, job.plan_number)
            request.session[configuration_key] = configuration
            request.session[previewed_key] = True
            _, validation_issues = validate_survey_point_records(job, records, captured_by=request.user)
            all_issues = structural_issues + validation_issues
            invalid_rows = len({issue["row"] for issue in all_issues if 0 < issue["row"] <= len(records)})
            return _render_entry(
                request,
                job,
                phase="preview",
                records=records,
                import_errors=all_issues,
                mapping_configuration=configuration,
                detected_count=len(records),
                valid_count=max(len(records) - len({issue["row"] for issue in all_issues if issue["row"] > 0}), 0),
                error_count=invalid_rows,
                duplicate_count=sum(
                    "duplicate" in issue["message"].lower() or "already exists" in issue["message"].lower()
                    for issue in all_issues
                ),
            )

        if action == "confirm_import":
            stage = request.session.get(stage_key)
            configuration = request.session.get(configuration_key)
            if not stage or not configuration or not request.session.get(previewed_key):
                return _render_entry(request, job, upload_error="Your staged file has expired. Select it again.")
            expected_records, structural_issues = build_import_records(stage, configuration, job.plan_number)
            try:
                records = json.loads(request.POST.get("records_json", "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                records = []
            if not isinstance(records, list):
                records = []
            if structural_issues or len(records) != len(expected_records):
                return redirect("survey_points:import_mapping", job_pk=job.pk)
            points, issues = save_survey_point_records(
                job=job, user=request.user, rows=records, request=request
            )
            if issues:
                invalid_rows = len({issue["row"] for issue in issues if 0 < issue["row"] <= len(records)})
                return _render_entry(
                    request,
                    job,
                    phase="preview",
                    records=records,
                    import_errors=issues,
                    detected_count=len(records),
                    valid_count=max(len(records) - len({issue["row"] for issue in issues if issue["row"] > 0}), 0),
                    error_count=invalid_rows,
                    duplicate_count=sum(
                        "duplicate" in issue["message"].lower() or "already exists" in issue["message"].lower()
                        for issue in issues
                    ),
                )
            request.session.pop(stage_key, None)
            request.session.pop(configuration_key, None)
            request.session.pop(previewed_key, None)
            return _render_entry(
                request,
                job,
                phase="results",
                imported_count=len(points),
            )

    selected_mode = "batch" if request.GET.get("mode") == "batch" else "manual"
    return _render_entry(
        request,
        job,
        manual_formset=ManualSurveyPointFormSet(prefix="points"),
        selected_mode=selected_mode,
    )


@login_required
def survey_point_import_cancel(request, job_pk):
    job = _authorized_job(request, job_pk)
    stage_key = f"survey-point-import-{job.pk}"
    request.session.pop(stage_key, None)
    request.session.pop(f"{stage_key}-configuration", None)
    request.session.pop(f"{stage_key}-previewed", None)
    return redirect("survey_points:create", job_pk=job.pk)


@login_required
def survey_point_import_mapping(request, job_pk):
    job = _authorized_job(request, job_pk)
    stage = request.session.get(f"survey-point-import-{job.pk}")
    if not stage:
        return redirect("survey_points:create", job_pk=job.pk)
    headers = stage["rows"][0] if stage["mode_guess"] == "columns" else []
    suggestions = suggest_header_mapping(headers)
    return _render_entry(
        request,
        job,
        phase="mapping",
        stage=stage,
        mapping_preview=_mapping_preview(stage),
        detected_row_count=len(stage["rows"]),
        headers=headers,
        suggested_mapping=suggestions,
        likely_header=len(suggestions) >= 2,
    )
