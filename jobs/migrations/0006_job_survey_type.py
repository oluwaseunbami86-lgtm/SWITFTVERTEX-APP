from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0005_remove_completion_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="job",
            name="survey_type",
            field=models.CharField(
                choices=[
                    ("boundary", "Boundary survey"),
                    ("topographic", "Topographic survey"),
                    ("layout", "Layout/subdivision"),
                    ("engineering", "Engineering survey"),
                    ("other", "Other"),
                ],
                default="boundary",
                max_length=20,
            ),
        ),
    ]
