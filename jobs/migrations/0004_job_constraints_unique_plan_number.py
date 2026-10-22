import django.core.validators
import django.db.models.deletion
import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0001_initial'),
        ('jobs', '0003_normalise_plan_numbers'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='job',
            name='client',
            field=models.ForeignKey(blank=True, help_text='Optional', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='jobs', to='clients.client'),
        ),
        migrations.AlterField(
            model_name='job',
            name='plan_number',
            field=models.CharField(help_text='e.g. OD/1379/2026/041', max_length=100, unique=True, validators=[django.core.validators.RegexValidator('^[A-Z]{1,10}/[0-9]+/[0-9]{4}/[0-9]{3,}$', 'PLAN NUMBER FORMAT IS NOT VALID - use the form OD/1379/2026/041 (letters / number / year / three-digit sequence).')]),
        ),
        migrations.AddIndex(
            model_name='job',
            index=models.Index(fields=['job_date'], name='job_date_idx'),
        ),
        migrations.AddIndex(
            model_name='job',
            index=models.Index(fields=['status'], name='job_status_idx'),
        ),
        migrations.AddIndex(
            model_name='job',
            index=models.Index(fields=['lifecycle'], name='job_lifecycle_idx'),
        ),
        migrations.AddIndex(
            model_name='job',
            index=models.Index(fields=['created_at'], name='job_created_idx'),
        ),
        migrations.AddConstraint(
            model_name='job',
            constraint=models.CheckConstraint(check=models.Q(('completion_date__isnull', True), ('completion_date__gte', models.F('job_date')), _connector='OR'), name='job_completion_not_before_start'),
        ),
        migrations.AddConstraint(
            model_name='job',
            constraint=models.CheckConstraint(check=models.Q(('plan_number__regex', '^[A-Z]{1,10}/[0-9]+/[0-9]{4}/[0-9]{3,}$')), name='job_plan_number_format'),
        ),
        migrations.AddConstraint(
            model_name='job',
            constraint=models.CheckConstraint(check=models.Q(('plan_number', django.db.models.functions.text.Upper('plan_number'))), name='job_plan_number_uppercase'),
        ),
        migrations.AddConstraint(
            model_name='job',
            constraint=models.CheckConstraint(check=models.Q(('survey_area__gt', 0)), name='job_survey_area_positive'),
        ),
        migrations.AddConstraint(
            model_name='job',
            constraint=models.CheckConstraint(check=models.Q(('lifecycle__in', ['active', 'archived', 'deleted'])), name='job_lifecycle_valid'),
        ),
        migrations.AddConstraint(
            model_name='job',
            constraint=models.CheckConstraint(check=models.Q(('status__in', ['assigned', 'in_progress', 'completed', 'on_hold'])), name='job_status_valid'),
        ),
        migrations.AddConstraint(
            model_name='project',
            constraint=models.UniqueConstraint(fields=('client', 'name'), name='uniq_project_name_per_client'),
        ),
    ]
