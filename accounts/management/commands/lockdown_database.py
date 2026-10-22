from django.core.management.base import BaseCommand
from django.db import connection

SQL = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.tablename);
    END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
"""


class Command(BaseCommand):
    help = (
        "Supabase only: turn on row-level security (with no policies) for every table and remove the "
        "anon/authenticated API roles' access, so survey data can never be read through Supabase's public "
        "REST API. Django connects as the table owner and is unaffected. Safe to run repeatedly; run it after "
        "every migrate (build.sh does)."
    )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stdout.write("Not PostgreSQL - nothing to lock down.")
            return
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")
            if cur.fetchone()[0] < 2:
                self.stdout.write("No Supabase API roles on this server - nothing to lock down.")
                return
            cur.execute(SQL)
            cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND NOT rowsecurity")
            unprotected = cur.fetchone()[0]
        if unprotected:
            self.stderr.write(self.style.ERROR(f"WARNING: {unprotected} table(s) still have row-level security OFF."))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("All public tables are locked down from the Supabase API."))
