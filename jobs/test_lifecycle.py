"""Data-integrity, permission, lifecycle and audit tests for the survey archive."""
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import Profile
from audit.models import AuditEvent
from clients.models import Client
from survey_points.models import SurveyPoint

from .models import Job

PLAN = "OD/1379/2026/041"


def make_user(name, role, superuser=False):
    u = User.objects.create_user(username=name, password="pw-12345-x", is_superuser=superuser)
    u.profile.role = role
    u.profile.status = Profile.Status.APPROVED
    u.profile.save()
    return u


def make_job(owner=None, plan=PLAN, **kw):
    defaults = dict(job_type="boundary", job_date="2026-01-15", title="Job", job_address="1 Road",
                    survey_area="100.000", area_unit="sqm", plan_number=plan, created_by=owner)
    defaults.update(kw)
    job = Job.objects.create(**defaults)
    SurveyPoint.objects.create(job=job, beacon_number="B1", northing="1.0000", easting="2.0000", height="3.0000")
    return job


def form_data(job=None, **over):
    data = {
        "job_type": "boundary", "job_date": "2026-01-15", "title": "T", "client": "",
        "project": "", "job_address": "Addr", "survey_area": "10.000", "area_unit": "sqm",
        "plan_number": "OD/1/2026/002", "has_reference_number": "", "reference_number": "", "status": "assigned",
        "assigned_to": "", "description": "", "expected_version": job.row_version if job else "",
        "beacons-TOTAL_FORMS": "1", "beacons-INITIAL_FORMS": "0", "beacons-MIN_NUM_FORMS": "0",
        "beacons-MAX_NUM_FORMS": "1000", "beacons-0-beacon_number": "B1", "beacons-0-northing": "1.0000",
        "beacons-0-easting": "2.0000", "beacons-0-height": "3.0000",
    }
    data.update(over)
    return data


class LoginMixin:
    def login(self, user):
        self.client.logout()
        self.assertTrue(self.client.login(username=user.username, password="pw-12345-x"))


class PlanNumberTests(LoginMixin, TestCase):
    def setUp(self):
        self.surveyor = make_user("s1", "surveyor")
        self.login(self.surveyor)

    def test_duplicate_plan_number_is_loud_and_links_existing_record(self):
        existing = make_job(self.surveyor)
        resp = self.client.post(reverse("jobs:create"), form_data(plan_number=PLAN))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "JOB NUMBER ALREADY EXISTS")
        self.assertContains(resp, reverse("jobs:detail", args=[existing.pk]))
        self.assertEqual(Job.objects.count(), 1)

    def test_duplicate_detected_case_insensitively_and_input_is_normalised(self):
        make_job(self.surveyor)
        resp = self.client.post(reverse("jobs:create"), form_data(plan_number=" od/1379/2026/041 "))
        self.assertContains(resp, "JOB NUMBER ALREADY EXISTS")
        resp = self.client.post(reverse("jobs:create"), form_data(plan_number=" od/5/2026/007 "))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.get(title="T").plan_number, "OD/5/2026/007")

    def test_bad_format_rejected(self):
        resp = self.client.post(reverse("jobs:create"), form_data(plan_number="PL-1001"))
        self.assertContains(resp, "PLAN NUMBER FORMAT IS NOT VALID")
        self.assertEqual(Job.objects.count(), 0)

    def test_database_itself_refuses_bad_rows(self):
        make_job(self.surveyor)
        for kwargs in (dict(plan=PLAN), dict(plan="nope"), dict(plan="OD/2/2026/002", survey_area="0")):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Job.objects.bulk_create([Job(job_type="boundary", job_date="2026-01-15", title="x", job_address="y",
                                             survey_area=kwargs.get("survey_area", "5"), area_unit="sqm",
                                             plan_number=kwargs["plan"])])


class PermissionTests(LoginMixin, TestCase):
    def setUp(self):
        self.root = make_user("root", Profile.Role.CLIENT_VIEWER, superuser=True)
        self.admin = make_user("adm", "admin")
        self.manager = make_user("mgr", Profile.Role.OFFICE_STAFF)
        self.surveyor = make_user("sur", "surveyor")
        self.other = make_user("oth", "surveyor")
        self.gis = make_user("gis", "surveyor")
        self.tech = make_user("tec", Profile.Role.OFFICE_STAFF)
        self.viewer = make_user("vwr", Profile.Role.CLIENT_VIEWER)
        self.client_obj = Client.objects.create(name="Permission client")
        self.viewer.profile.client = self.client_obj
        self.viewer.profile.save()
        self.job = make_job(self.surveyor, client=self.client_obj)

    def status(self, user, name, *args, method="get", data=None):
        self.login(user)
        return getattr(self.client, method)(reverse(name, args=args), data or {}).status_code

    def test_viewer_is_read_only(self):
        self.assertEqual(self.status(self.viewer, "jobs:detail", self.job.pk), 200)
        self.assertEqual(self.status(self.viewer, "jobs:create"), 403)
        self.assertEqual(self.status(self.viewer, "jobs:update", self.job.pk), 403)
        self.assertEqual(self.status(self.viewer, "documents:upload", self.job.pk), 403)
        self.assertEqual(self.status(self.viewer, "survey_points:create", self.job.pk), 403)
        self.assertEqual(self.status(self.viewer, "clients:create"), 403)

    def test_changing_the_url_does_not_bypass_ownership(self):
        # another surveyor guessing the job id in the URL is refused server-side
        self.assertEqual(self.status(self.other, "jobs:update", self.job.pk), 403)
        self.assertEqual(self.status(self.other, "jobs:update", self.job.pk, method="post", data=form_data(self.job)), 403)
        self.assertEqual(self.status(self.surveyor, "jobs:update", self.job.pk), 200)
        self.assertEqual(self.status(self.manager, "jobs:update", self.job.pk), 200)

    def test_assigned_surveyor_and_office_staff_can_edit_jobs_and_beacons(self):
        self.job.assigned_to = self.gis
        self.job.save()
        self.assertEqual(self.status(self.gis, "jobs:update", self.job.pk), 200)
        self.job.assigned_to = self.tech
        self.job.save()
        self.assertEqual(self.status(self.tech, "jobs:update", self.job.pk), 200)
        self.assertEqual(self.status(self.tech, "survey_points:create", self.job.pk), 200)
        self.assertEqual(self.status(self.tech, "jobs:create"), 200)
        other_job = make_job(self.surveyor, plan="OD/9/2026/009")
        self.assertEqual(self.status(self.tech, "survey_points:create", other_job.pk), 200)

    def test_only_admin_and_super_admin_can_archive_and_delete(self):
        for u in (self.surveyor, self.gis, self.tech, self.viewer):
            self.assertEqual(self.status(u, "jobs:archive", self.job.pk), 403)
        self.assertEqual(self.status(self.manager, "jobs:archive", self.job.pk), 403)
        self.assertEqual(self.status(self.manager, "jobs:delete", self.job.pk), 403)
        self.assertEqual(self.status(self.manager, "jobs:purge", self.job.pk), 403)
        self.assertEqual(self.status(self.admin, "jobs:archive", self.job.pk), 200)

    def test_superuser_is_super_admin_regardless_of_profile(self):
        self.assertEqual(self.status(self.root, "jobs:archive", self.job.pk), 200)


class LifecycleAndAuditTests(LoginMixin, TestCase):
    def setUp(self):
        self.admin = make_user("adm", "admin")
        self.manager = make_user("mgr", Profile.Role.OFFICE_STAFF)
        self.job = make_job(self.admin, title="Warri-unique-title")

    def post(self, user, name, data=None):
        self.login(user)
        return self.client.post(reverse(name, args=[self.job.pk]), data or {})

    def test_full_lifecycle_archive_delete_restore_purge_with_audit(self):
        self.post(self.admin, "jobs:archive")
        self.job.refresh_from_db()
        self.assertEqual(self.job.lifecycle, "archived")
        # archived job is read-only
        self.login(self.admin)
        resp = self.client.post(reverse("jobs:update", args=[self.job.pk]), form_data(self.job, plan_number=PLAN, title="HACK"))
        self.assertEqual(resp.status_code, 302)
        self.job.refresh_from_db()
        self.assertNotEqual(self.job.title, "HACK")
        # cannot purge or soft-delete in the wrong state
        self.post(self.admin, "jobs:purge", {"confirm_text": f"DELETE {PLAN}"})
        self.assertTrue(Job.all_objects.filter(pk=self.job.pk).exists())
        self.post(self.admin, "jobs:delete")
        self.assertFalse(Job.objects.filter(pk=self.job.pk).exists())          # hidden from normal queries
        self.assertTrue(Job.all_objects.filter(pk=self.job.pk).exists())       # but not destroyed
        self.assertNotContains(self.client.get(reverse("jobs:list") + "?show=all"), "Warri-unique-title")
        self.assertEqual(self.client.get(reverse("dashboard:search") + "?q=OD/1379").context["jobs"].count(), 0)
        self.assertEqual(self.client.get(reverse("jobs:detail", args=[self.job.pk])).status_code, 200)  # admin may see
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse("jobs:detail", args=[self.job.pk])).status_code, 404)
        self.post(self.admin, "jobs:restore")
        self.assertTrue(Job.objects.filter(pk=self.job.pk, lifecycle="active").exists())

    def test_purge_requires_exact_typed_phrase_then_destroys_and_audits(self):
        self.post(self.admin, "jobs:archive"); self.post(self.admin, "jobs:delete")
        self.post(self.admin, "jobs:purge", {"confirm_text": "yes"})
        self.assertTrue(Job.all_objects.filter(pk=self.job.pk).exists())
        self.assertEqual(SurveyPoint.objects.count(), 1)
        self.post(self.admin, "jobs:purge", {"confirm_text": f"DELETE {PLAN}"})
        self.assertFalse(Job.all_objects.filter(pk=self.job.pk).exists())
        self.assertEqual(SurveyPoint.objects.count(), 0)
        event = AuditEvent.objects.get(action="job_purged")
        self.assertEqual(event.entity_label, PLAN)
        self.assertEqual(event.username, "adm")
        self.assertEqual(len(event.old_value["beacons"]), 1)
        actions = list(AuditEvent.objects.order_by("id").values_list("action", flat=True))
        self.assertEqual(actions, ["job_archived", "job_soft_deleted", "job_purged"])

    def test_job_cannot_be_hard_deleted_while_it_has_children(self):
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            self.job.delete()

    def test_audit_history_is_append_only(self):
        self.post(self.admin, "jobs:archive")
        event = AuditEvent.objects.first()
        event.description = "tampered"
        with self.assertRaises(PermissionError):
            event.save()
        with self.assertRaises(PermissionError):
            event.delete()
        with self.assertRaises(PermissionError):
            AuditEvent.objects.all().update(description="x")
        with self.assertRaises(PermissionError):
            AuditEvent.objects.all().delete()

    def test_create_and_edit_are_audited_with_old_and_new_values(self):
        self.login(self.admin)
        self.client.post(reverse("jobs:create"), form_data(plan_number="OD/7/2026/001", title="First"))
        job = Job.objects.get(plan_number="OD/7/2026/001")
        self.assertTrue(AuditEvent.objects.filter(action="job_created", entity_id=str(job.pk)).exists())
        self.client.post(reverse("jobs:update", args=[job.pk]), form_data(job, plan_number="OD/7/2026/001", title="Second", status="completed"))
        event = AuditEvent.objects.get(entity_id=str(job.pk), action="job_updated")
        self.assertEqual(event.old_value["title"], "First")
        self.assertEqual(event.new_value["title"], "Second")


class ConcurrencyAndClientTests(LoginMixin, TestCase):
    def setUp(self):
        self.admin = make_user("adm", "admin")
        self.login(self.admin)

    def test_stale_edit_is_refused_not_silently_overwritten(self):
        job = make_job(self.admin)
        stale = form_data(job, plan_number=PLAN, title="Alice's edit")           # Alice opened version 1
        self.client.post(reverse("jobs:update", args=[job.pk]), form_data(job, plan_number=PLAN, title="Bob's edit"))
        job.refresh_from_db()
        self.assertEqual((job.title, job.row_version), ("Bob's edit", 2))
        resp = self.client.post(reverse("jobs:update", args=[job.pk]), stale)    # Alice saves version-1 form
        self.assertContains(resp, "CHANGED BY SOMEONE ELSE")
        job.refresh_from_db()
        self.assertEqual(job.title, "Bob's edit")

    def test_client_with_jobs_cannot_be_deleted(self):
        c = Client.objects.create(name="Acme")
        make_job(self.admin, client=c)
        resp = self.client.post(reverse("clients:delete", args=[c.pk]), follow=True)
        self.assertContains(resp, "CANNOT BE DELETED")
        self.assertTrue(Client.objects.filter(pk=c.pk).exists())

    def test_client_without_jobs_can_be_deleted_and_is_audited(self):
        c = Client.objects.create(name="Gone")
        self.client.post(reverse("clients:delete", args=[c.pk]))
        self.assertFalse(Client.objects.filter(pk=c.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action="client_deleted", entity_label="Gone").exists())
