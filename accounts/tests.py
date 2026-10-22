from io import BytesIO
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from PIL import Image

from clients.models import Client
from documents.models import Document
from jobs.models import Job
from survey_points.models import SurveyPoint

from .models import Profile

User = get_user_model()
PASSWORD = "Test-user!_93Garden"


def make_user(username, role=Profile.Role.CLIENT_VIEWER, *, superuser=False, status=Profile.Status.APPROVED):
    user = User.objects.create_user(username=username, password=PASSWORD, is_superuser=superuser)
    user.profile.role = role
    user.profile.status = status
    user.profile.save()
    return user


class ApprovalFlowTests(TestCase):
    def setUp(self):
        self.super_admin = make_user("root", superuser=True)

    def test_pending_rejected_and_suspended_users_cannot_log_in_with_status_message(self):
        cases = (
            (Profile.Status.PENDING, "Your account is awaiting approval by the administrator."),
            (Profile.Status.REJECTED, "Your account has been rejected. Reason: incomplete details"),
            (Profile.Status.SUSPENDED, "Your account has been suspended. Contact the administrator."),
        )
        for index, (status, message) in enumerate(cases):
            with self.subTest(status=status):
                user = make_user(f"blocked{index}", status=status)
                if status == Profile.Status.REJECTED:
                    user.profile.rejection_reason = "incomplete details"
                    user.profile.save()
                self.assertFalse(self.client.login(username=user.username, password=PASSWORD))
                response = self.client.post(reverse("login"), {
                    "username": user.username,
                    "password": PASSWORD,
                })
                self.assertContains(response, message)

    def test_approved_user_can_log_in(self):
        user = make_user("approved")
        self.assertTrue(self.client.login(username=user.username, password=PASSWORD))
        self.assertEqual(self.client.get(reverse("accounts:my_profile")).status_code, 200)

    def test_signup_ignores_submitted_role_and_does_not_log_user_in(self):
        response = self.client.post(reverse("signup"), {
            "username": "new-client",
            "full_name": "New Applicant",
            "email": "Applicant@example.com",
            "phone_number": "12345",
            "password1": "b8D!xW2#pQ5@kL9",
            "password2": "b8D!xW2#pQ5@kL9",
            "role": Profile.Role.ADMIN,
        })
        user = User.objects.get(username="new-client")
        self.assertRedirects(response, reverse("signup_pending"))
        self.assertEqual(user.profile.role, Profile.Role.CLIENT_VIEWER)
        self.assertEqual(user.profile.status, Profile.Status.PENDING)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotIn("role", response.context["form"].fields if response.context else {})

    def test_signup_rejects_case_insensitive_duplicate_username_and_email(self):
        existing = make_user("taken", status=Profile.Status.APPROVED)
        existing.email = "same@example.com"
        existing.save()
        for values in (
            {"username": "TAKEN", "email": "new@example.com"},
            {"username": "available", "email": "SAME@example.com"},
        ):
            data = {
                "username": values["username"],
                "full_name": "Duplicate Test",
                "email": values["email"],
                "password1": "b8D!xW2#pQ5@kL9",
                "password2": "b8D!xW2#pQ5@kL9",
            }
            response = self.client.post(reverse("signup"), data)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(User.objects.count(), 2)

    def test_honeypot_rejects_signup(self):
        response = self.client.post(reverse("signup"), {
            "username": "bot",
            "full_name": "Spam Bot",
            "email": "bot@example.com",
            "password1": "b8D!xW2#pQ5@kL9",
            "password2": "b8D!xW2#pQ5@kL9",
            "website": "spam",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="bot").exists())

    def test_non_superusers_cannot_reach_approval_or_user_management(self):
        admin = make_user("app-admin", role=Profile.Role.ADMIN)
        self.client.force_login(admin)
        for route in ("accounts:pending_approvals", "accounts:users", "accounts:audit_log"):
            self.assertEqual(self.client.get(reverse(route)).status_code, 403)

    def test_super_admin_users_page_shows_user_details_and_management_controls(self):
        member = make_user("managed-member", role=Profile.Role.OFFICE_STAFF)
        self.client.force_login(self.super_admin)
        response = self.client.get(reverse("accounts:users"), {"selected": member.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage system users, roles and access permissions.")
        self.assertContains(response, "Role management")
        self.assertContains(response, "Password management")
        self.assertContains(response, "managed-member")

    def test_role_update_keeps_the_user_detail_panel_open(self):
        member = make_user("role-member", role=Profile.Role.OFFICE_STAFF)
        self.client.force_login(self.super_admin)
        response = self.client.post(reverse("accounts:user_role", args=[member.pk]), {
            "role": Profile.Role.SURVEYOR,
            "client": "",
        })
        self.assertRedirects(response, f"{reverse('accounts:users')}?selected={member.pk}")
        member.profile.refresh_from_db()
        self.assertEqual(member.profile.role, Profile.Role.SURVEYOR)

    def test_super_admin_cannot_delete_last_active_super_admin(self):
        self.client.force_login(self.super_admin)
        response = self.client.post(reverse("accounts:user_delete", args=[self.super_admin.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.super_admin.pk, is_superuser=True).exists())

    def test_suspended_logged_in_user_is_logged_out_on_next_request(self):
        user = make_user("on-duty")
        self.client.force_login(user)
        user.profile.status = Profile.Status.SUSPENDED
        user.profile.save()

        response = self.client.get(reverse("accounts:my_profile"), follow=True)
        self.assertContains(response, "Your account has been suspended. Contact the administrator.")
        self.assertNotIn("_auth_user_id", self.client.session)


class AccountRolePermissionTests(TestCase):
    def setUp(self):
        self.root = make_user("root", superuser=True)
        self.admin = make_user("admin", Profile.Role.ADMIN)
        self.office = make_user("office", Profile.Role.OFFICE_STAFF)
        self.surveyor = make_user("surveyor", Profile.Role.SURVEYOR)
        self.other_surveyor = make_user("other-surveyor", Profile.Role.SURVEYOR)
        self.client_viewer = make_user("client-viewer", Profile.Role.CLIENT_VIEWER)
        self.client_record = Client.objects.create(name="Linked client", created_by=self.office)
        self.other_client = Client.objects.create(name="Other client", created_by=self.office)
        self.client_viewer.profile.client = self.client_record
        self.client_viewer.profile.save()
        self.job = self.make_job("OD/1/2026/001", self.client_record, self.surveyor)
        self.other_job = self.make_job("OD/2/2026/002", self.other_client, self.other_surveyor)
        SurveyPoint.objects.create(
            job=self.job, beacon_number="B1", northing="1", easting="2", height="3",
            captured_by=self.surveyor,
        )
        self.document = Document.objects.create(job=self.job, title="Visible document")
        self.other_document = Document.objects.create(job=self.other_job, title="Private document")

    def make_job(self, plan, client, owner):
        return Job.objects.create(
            job_type="boundary",
            job_date="2026-01-15",
            title=f"Job {plan}",
            client=client,
            job_address="Test address",
            survey_area="10.000",
            area_unit="sqm",
            plan_number=plan,
            created_by=owner,
            assigned_to=owner,
        )

    def request_as(self, user, route, *args, method="get", data=None):
        self.client.logout()
        self.client.force_login(user)
        return getattr(self.client, method)(reverse(route, args=args), data or {})

    def test_job_visibility_is_limited_to_assignment_or_linked_client(self):
        response = self.request_as(self.surveyor, "jobs:list")
        self.assertContains(response, self.job.title)
        self.assertNotContains(response, self.other_job.title)
        self.assertEqual(self.request_as(self.other_surveyor, "jobs:detail", self.job.pk).status_code, 404)
        self.assertEqual(self.request_as(self.client_viewer, "jobs:detail", self.job.pk).status_code, 200)
        self.assertEqual(self.request_as(self.client_viewer, "jobs:detail", self.other_job.pk).status_code, 404)

    def test_client_viewer_can_read_only_their_jobs_beacons_and_documents(self):
        self.assertEqual(self.request_as(self.client_viewer, "documents:list").status_code, 200)
        response = self.request_as(self.client_viewer, "documents:list")
        self.assertContains(response, "Visible document")
        self.assertNotContains(response, "Private document")
        self.assertEqual(self.request_as(self.client_viewer, "documents:detail", self.document.pk).status_code, 200)
        self.assertEqual(self.request_as(self.client_viewer, "documents:detail", self.other_document.pk).status_code, 404)
        self.assertEqual(self.request_as(self.client_viewer, "clients:list").status_code, 403)
        self.assertEqual(self.request_as(self.client_viewer, "jobs:update", self.job.pk).status_code, 403)
        self.assertEqual(self.request_as(self.client_viewer, "documents:upload", self.job.pk).status_code, 403)

    def test_surveyor_and_office_staff_cannot_delete_jobs_or_clients_or_view_reports(self):
        for user in (self.surveyor, self.office):
            with self.subTest(role=user.profile.role):
                self.assertEqual(self.request_as(user, "jobs:delete", self.job.pk).status_code, 403)
                self.assertEqual(self.request_as(user, "clients:delete", self.client_record.pk).status_code, 403)
        self.assertEqual(self.request_as(self.surveyor, "clients:list").status_code, 403)
        self.assertEqual(self.request_as(self.surveyor, "reports:home").status_code, 403)
        self.assertEqual(self.request_as(self.surveyor, "survey_points:create", self.job.pk).status_code, 200)
        self.assertEqual(self.request_as(self.office, "survey_points:create", self.job.pk).status_code, 200)
        self.assertEqual(self.request_as(self.office, "reports:home").status_code, 200)
        self.assertEqual(self.request_as(self.admin, "reports:home").status_code, 200)

    def test_admin_can_delete_jobs_and_clients_and_office_cannot(self):
        self.job.lifecycle = Job.Lifecycle.ARCHIVED
        self.job.save()
        self.assertEqual(self.request_as(self.office, "jobs:delete", self.job.pk).status_code, 403)
        self.assertEqual(self.request_as(self.admin, "jobs:delete", self.job.pk).status_code, 200)
        self.assertEqual(self.request_as(self.admin, "clients:delete", self.other_client.pk).status_code, 200)
        self.assertEqual(self.request_as(self.office, "clients:delete", self.other_client.pk).status_code, 403)

    def test_profile_form_cannot_change_role_or_status(self):
        self.client.force_login(self.surveyor)
        response = self.client.post(reverse("accounts:my_profile"), {
            "role": Profile.Role.ADMIN,
            "status": Profile.Status.APPROVED,
            "phone_number": "555",
            "full_name": "Surveyor Example",
            "email": "surveyor@example.com",
        })
        self.assertEqual(response.status_code, 302)
        self.surveyor.profile.refresh_from_db()
        self.assertEqual(self.surveyor.profile.role, Profile.Role.SURVEYOR)
        self.assertEqual(self.surveyor.profile.status, Profile.Status.APPROVED)


class SettingsPageTests(TestCase):
    def setUp(self):
        self.user = make_user("settings-user")
        self.user.email = "settings@example.com"
        self.user.set_password("Existing!Password49")
        self.user.save()
        self.client.force_login(self.user)

    def test_profile_details_save_without_an_optional_picture(self):
        response = self.client.post(reverse("accounts:my_profile"), {
            "full_name": "Ada Surveyor",
            "email": "ada@example.com",
            "phone_number": "",
        })
        self.assertRedirects(response, reverse("accounts:my_profile"))
        self.user.refresh_from_db()
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.get_full_name(), "Ada Surveyor")
        self.assertEqual(self.user.email, "ada@example.com")
        self.assertFalse(self.user.profile.profile_picture)

    def test_profile_picture_can_be_uploaded_and_removed(self):
        image_bytes = BytesIO()
        Image.new("RGB", (16, 16), color="teal").save(image_bytes, format="PNG")
        upload = SimpleUploadedFile("portrait.png", image_bytes.getvalue(), content_type="image/png")

        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(reverse("accounts:my_profile"), {
                "full_name": "Settings User",
                "email": self.user.email,
                "phone_number": "",
                "profile_picture": upload,
            })
            self.assertRedirects(response, reverse("accounts:my_profile"))
            self.user.profile.refresh_from_db()
            old_picture = self.user.profile.profile_picture.name
            self.assertTrue(self.user.profile.profile_picture.storage.exists(old_picture))
            profile_response = self.client.get(reverse("accounts:my_profile"))
            self.assertContains(profile_response, self.user.profile.profile_picture.url, count=3)
            self.assertContains(profile_response, "URL.createObjectURL(file)")

            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse("accounts:my_profile"), {
                    "full_name": "Settings User",
                    "email": self.user.email,
                    "phone_number": "",
                    "remove_profile_picture": "on",
                })
            self.assertRedirects(response, reverse("accounts:my_profile"))
            self.user.profile.refresh_from_db()
            self.assertFalse(self.user.profile.profile_picture)
            self.assertFalse(self.user.profile.profile_picture.storage.exists(old_picture))

    def test_profile_picture_rejects_unsupported_formats(self):
        response = self.client.post(reverse("accounts:my_profile"), {
            "full_name": "Settings User",
            "email": self.user.email,
            "phone_number": "",
            "profile_picture": SimpleUploadedFile(
                "portrait.gif", b"not an image", content_type="image/gif"
            ),
        })
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.profile_picture)

    def test_password_change_uses_django_validation_and_keeps_session(self):
        response = self.client.post(reverse("accounts:change_password"), {
            "old_password": "Existing!Password49",
            "new_password1": "A-new!Password7392",
            "new_password2": "A-new!Password7392",
        })
        self.assertRedirects(response, reverse("accounts:change_password"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("A-new!Password7392"))
        self.assertEqual(self.client.get(reverse("accounts:my_profile")).status_code, 200)

    def test_delete_account_settings_respects_admin_managed_deletion(self):
        response = self.client.get(reverse("accounts:delete_account_info"))
        self.assertContains(response, "Account deletion is managed by a Super Admin")
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
