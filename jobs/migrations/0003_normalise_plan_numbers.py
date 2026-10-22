import re

from django.db import migrations

# Plan numbers must look like OD/1379/2026/041. Older records may have been typed
# as OD/1379/2026/41 or in lower case. This step ONLY: strips spaces, upper-cases,
# and zero-pads the final sequence to three digits. It never invents data. The
# original text is kept in legacy_plan_number so the step is fully reversible.
# If two records would end up with the same plan number, the migration STOPS
# with a clear message instead of guessing - fix the duplicates and re-run.


def normalise(value):
    value = value.strip().upper()
    parts = value.split("/")
    if len(parts) == 4 and parts[3].isdigit() and len(parts[3]) < 3:
        parts[3] = parts[3].zfill(3)
    return "/".join(parts)


def forwards(apps, schema_editor):
    Job = apps.get_model("jobs", "Job")
    seen = {}
    problems = []
    rows = list(Job.objects.all().order_by("pk"))
    for job in rows:
        new = normalise(job.plan_number)
        if new in seen:
            problems.append(f"job #{job.pk} and job #{seen[new]} would both be {new}")
        seen[new] = job.pk
    if problems:
        raise RuntimeError(
            "MIGRATION STOPPED - duplicate plan numbers found; nothing was changed. "
            + "; ".join(problems)
        )
    pattern = re.compile(r"^[A-Z]{1,10}/[0-9]+/[0-9]{4}/[0-9]{3,}$")
    bad = [f"job #{j.pk}: {normalise(j.plan_number)!r}" for j in rows if not pattern.match(normalise(j.plan_number))]
    if bad:
        raise RuntimeError(
            "MIGRATION STOPPED - these plan numbers do not match the required format "
            "(e.g. OD/1379/2026/041); nothing was changed. Correct them first: " + "; ".join(bad)
        )
    for job in rows:
        new = normalise(job.plan_number)
        if new != job.plan_number:
            job.legacy_plan_number = job.plan_number
            job.plan_number = new
            job.save(update_fields=["plan_number", "legacy_plan_number"])


def backwards(apps, schema_editor):
    Job = apps.get_model("jobs", "Job")
    for job in Job.objects.exclude(legacy_plan_number=""):
        job.plan_number = job.legacy_plan_number
        job.legacy_plan_number = ""
        job.save(update_fields=["plan_number", "legacy_plan_number"])


class Migration(migrations.Migration):
    dependencies = [("jobs", "0002_job_lifecycle_project_fields")]
    operations = [migrations.RunPython(forwards, backwards)]
