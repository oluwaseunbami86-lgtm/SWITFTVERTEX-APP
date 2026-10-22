from django.core.management.base import BaseCommand
from django.utils import timezone

from documents.models import ReplacedFile
from documents.storage import StorageError, get_store


class Command(BaseCommand):
    help = "Delete the old content of overwritten files once their retention period has passed."

    def handle(self, *args, **opts):
        done = failed = 0
        for rep in ReplacedFile.objects.filter(purged_at__isnull=True, purge_after__lte=timezone.now()):
            try:
                get_store().delete(rep.storage_key)
            except StorageError as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"could not delete {rep.storage_key}: {exc}"))
                continue
            rep.purged_at = timezone.now()
            rep.save(update_fields=["purged_at"])
            done += 1
        self.stdout.write(f"Purged {done} expired replaced file(s); {failed} failure(s).")
        if failed:
            raise SystemExit(1)
