from django.core.management.base import BaseCommand

from documents.models import DocumentVersion, ReplacedFile
from documents.storage import get_store


class Command(BaseCommand):
    help = ("Compare the object store with the database. Reports (never silently fixes): stored objects with no "
            "database record (orphans, e.g. after a failed upload) and database records whose object is missing. "
            "--delete-orphans removes orphans older-looking than the safety check.")

    def add_arguments(self, parser):
        parser.add_argument("--delete-orphans", action="store_true")

    def handle(self, *args, **opts):
        known = set(DocumentVersion.objects.filter(storage_backend="object_store").values_list("storage_key", flat=True))
        known |= set(ReplacedFile.objects.filter(purged_at__isnull=True).values_list("storage_key", flat=True))
        store = get_store()
        stored = dict(store.list("jobs/"))
        orphans = sorted(k for k in stored if k not in known)
        missing = sorted(k for k in known if k not in stored)
        for k in orphans:
            self.stdout.write(f"ORPHAN (in storage, not in database): {k} ({stored[k]} bytes)")
        for k in missing:
            self.stderr.write(self.style.ERROR(f"MISSING (in database, not in storage): {k}"))
        if opts["delete_orphans"]:
            for k in orphans:
                store.delete(k)
                self.stdout.write(f"deleted orphan {k}")
        self.stdout.write(f"{len(stored)} object(s) in storage, {len(known)} referenced, {len(orphans)} orphan(s), {len(missing)} missing.")
        if missing:
            raise SystemExit(1)
