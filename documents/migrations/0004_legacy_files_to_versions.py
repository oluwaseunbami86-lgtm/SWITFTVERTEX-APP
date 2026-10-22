import hashlib

from django.core.files.storage import default_storage
from django.db import migrations

# Every document uploaded before versioning becomes "version 1" of itself. The file is NOT moved
# or touched here: the version simply points at the existing file (backend = legacy_media) and is
# marked unverified/missing honestly. Use `manage.py import_legacy_files` to move such files into
# R2 with checksum verification. Reversible: reversing just removes the version rows.


def forwards(apps, schema_editor):
    Document = apps.get_model("documents", "Document")
    Version = apps.get_model("documents", "DocumentVersion")
    for doc in Document.objects.exclude(file=""):
        if Version.objects.filter(document=doc).exists():
            continue
        name = doc.file.name
        filename = name.rsplit("/", 1)[-1]
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        size, sha, status = 0, "", "missing"
        try:
            if default_storage.exists(name):
                digest = hashlib.sha256()
                with default_storage.open(name, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        digest.update(chunk)
                        size += len(chunk)
                sha, status = digest.hexdigest(), "ok"
        except Exception:
            size, sha, status = 0, "", "unverified"
        Version.objects.create(
            document=doc, version_number=1, is_current=True, original_filename=filename, extension=ext[:10],
            size_bytes=size, sha256=sha, storage_key=name, storage_backend="legacy_media",
            change_note="Existing file imported when versioning was introduced", uploaded_by=doc.uploaded_by,
            integrity_status=status,
        )


def backwards(apps, schema_editor):
    apps.get_model("documents", "DocumentVersion").objects.filter(storage_backend="legacy_media", version_number=1).delete()


class Migration(migrations.Migration):
    dependencies = [("documents", "0003_versioned_documents")]
    operations = [migrations.RunPython(forwards, backwards)]
