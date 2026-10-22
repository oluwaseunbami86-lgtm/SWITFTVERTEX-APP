from django.db import migrations

# Old role -> new role. Mapped to the LEAST privileged equivalent on purpose:
#   office_staff  -> technician   (view/create, limited edit)
#   client_viewer -> viewer       (view only)
# Django superusers become Super Admin. Nobody is silently promoted to Admin.
MAP = {"office_staff": "technician", "client_viewer": "viewer"}


def forwards(apps, schema_editor):
    Profile = apps.get_model("accounts", "Profile")
    for old, new in MAP.items():
        Profile.objects.filter(role=old).update(role=new)
    Profile.objects.filter(user__is_superuser=True).update(role="super_admin")


def backwards(apps, schema_editor):
    Profile = apps.get_model("accounts", "Profile")
    Profile.objects.filter(role="technician").update(role="office_staff")
    Profile.objects.filter(role="viewer").update(role="client_viewer")
    Profile.objects.filter(role="super_admin").update(role="admin")


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_alter_profile_role")]
    operations = [migrations.RunPython(forwards, backwards)]
