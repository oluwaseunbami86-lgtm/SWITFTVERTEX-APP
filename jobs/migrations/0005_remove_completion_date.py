from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0004_job_constraints_unique_plan_number"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="job",
            name="job_completion_not_before_start",
        ),
        migrations.RemoveField(
            model_name="job",
            name="completion_date",
        ),
    ]
