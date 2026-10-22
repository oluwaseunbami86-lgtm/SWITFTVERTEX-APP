from django.core.management.base import BaseCommand

from documents.models import DocumentVersion
from documents.services import DocumentError, verify_version


class Command(BaseCommand):
    help = ("Re-read stored files and compare them with the recorded size and SHA-256. Exits with an error "
            "(non-zero) if ANY file is missing or damaged, so a scheduler/alert can notice. Use --limit for a "
            "rolling sample (oldest-verified first) when the archive is large.")

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="check at most N versions (least recently verified first)")

    def handle(self, *args, **opts):
        qs = DocumentVersion.objects.filter(storage_backend="object_store").select_related("document").order_by(
            "last_verified_at", "pk")
        if opts["limit"]:
            qs = qs[: opts["limit"]]
        checked = bad = 0
        for ver in qs:
            try:
                status = verify_version(ver)
            except DocumentError as exc:
                self.stderr.write(self.style.ERROR(f"CANNOT CHECK (storage unreachable): {exc}"))
                raise SystemExit(2)
            checked += 1
            if status != "ok":
                bad += 1
                self.stderr.write(self.style.ERROR(f"{status.upper()}: {ver.original_filename} v{ver.version_number} ({ver.storage_key})"))
        self.stdout.write(f"Checked {checked} file version(s); {bad} problem(s).")
        if bad:
            raise SystemExit(1)
