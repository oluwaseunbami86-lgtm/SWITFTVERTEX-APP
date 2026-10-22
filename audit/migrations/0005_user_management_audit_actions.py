from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("audit", "0004_versioned_documents")]

    operations = [
        migrations.AlterField(
            model_name="auditevent",
            name="action",
            field=models.CharField(
                choices=[
                    ("job_created", "Job created"),
                    ("job_updated", "Job updated"),
                    ("job_status_changed", "Job status changed"),
                    ("job_archived", "Job archived"),
                    ("job_restored", "Job restored"),
                    ("job_soft_deleted", "Job deleted (recoverable)"),
                    ("job_purged", "Job permanently deleted"),
                    ("beacons_changed", "Survey beacons changed"),
                    ("client_created", "Client created"),
                    ("client_updated", "Client updated"),
                    ("client_deleted", "Client deleted"),
                    ("document_uploaded", "Document uploaded"),
                    ("document_version_created", "Document version created"),
                    ("document_overwritten", "Document overwritten (confirmed)"),
                    ("document_version_restored", "Older version made current"),
                    ("document_downloaded", "Document downloaded"),
                    ("document_deleted", "Document deleted (recoverable)"),
                    ("document_restored", "Document restored"),
                    ("document_purged", "Document permanently deleted"),
                    ("user_approved", "User approved"),
                    ("user_rejected", "User rejected"),
                    ("user_suspended", "User suspended"),
                    ("user_reactivated", "User reactivated"),
                    ("user_role_changed", "User role changed"),
                    ("user_deleted", "User deleted"),
                    ("upload_failed", "File upload failed"),
                    ("file_integrity_failed", "File integrity check failed"),
                    ("denied", "Action refused"),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
    ]
