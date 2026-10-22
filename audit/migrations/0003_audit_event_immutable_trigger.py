from django.db import migrations

CREATE = """
CREATE OR REPLACE FUNCTION audit_event_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_auditevent rows are append-only (UPDATE/DELETE refused)';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_event_no_change ON audit_auditevent;
CREATE TRIGGER audit_event_no_change BEFORE UPDATE OR DELETE ON audit_auditevent
    FOR EACH ROW EXECUTE FUNCTION audit_event_immutable();
"""
DROP = """
DROP TRIGGER IF EXISTS audit_event_no_change ON audit_auditevent;
DROP FUNCTION IF EXISTS audit_event_immutable();
"""


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CREATE)


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(DROP)


class Migration(migrations.Migration):
    dependencies = [("audit", "0002_audit_event")]
    operations = [migrations.RunPython(forwards, backwards)]
