import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


ROLE_MAP = {
    "manager": "office_staff",
    "technician": "office_staff",
    "gis_analyst": "surveyor",
    "viewer": "client_viewer",
    "super_admin": "office_staff",
}


def approve_existing_users(apps, schema_editor):
    Profile = apps.get_model("accounts", "Profile")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    now = django.utils.timezone.now()

    for user in User.objects.all().iterator():
        profile, _ = Profile.objects.get_or_create(
            user_id=user.pk,
            defaults={"role": "client_viewer"},
        )
        role = ROLE_MAP.get(profile.role, profile.role)
        if user.is_superuser:
            role = "client_viewer"
        profile.role = role
        profile.status = "approved"
        profile.approved_at = now
        profile.status_changed_at = now
        profile.rejection_reason = ""
        profile.save(update_fields=[
            "role", "status", "approved_at", "status_changed_at", "rejection_reason",
        ])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_map_legacy_roles"),
        ("clients", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="client",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="viewer_profiles",
                to="clients.client",
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("suspended", "Suspended"),
                ],
                default="pending",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="approved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="approved_profiles",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="profile",
            name="rejection_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="profile",
            name="status_changed_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AlterField(
            model_name="profile",
            name="role",
            field=models.CharField(
                choices=[
                    ("admin", "Admin"),
                    ("surveyor", "Surveyor"),
                    ("office_staff", "Office staff"),
                    ("client_viewer", "Client viewer"),
                ],
                default="client_viewer",
                max_length=20,
            ),
        ),
        migrations.RunPython(approve_existing_users, migrations.RunPython.noop),
    ]
