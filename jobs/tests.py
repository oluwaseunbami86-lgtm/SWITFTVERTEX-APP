import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from clients.models import Client
from documents.models import Document
from documents.storage import reset_store
from survey_points.models import SurveyPoint

from .models import Job


class JobTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="surveyor1", password="testpass123")
        from accounts.models import Profile
        self.user.profile.role = Profile.Role.SURVEYOR
        self.user.profile.status = Profile.Status.APPROVED
        self.user.profile.save()
        self.client.login(username="surveyor1", password="testpass123")
        self.client_obj = Client.objects.create(name="Acme Estates", created_by=self.user)

    def _base_post_data(self, **overrides):
        data = {
            "job_type": "boundary",
            "job_date": "2026-01-15",
            "title": "Warri boundary survey",
            "client": self.client_obj.pk,
            "job_address": "12 Airport Road, Warri",
            "survey_area": "500.000",
            "area_unit": "sqm",
            "plan_number": "OD/1001/2026/001",
            "has_reference_number": "",
            "reference_number": "",
            "status": "assigned",
            "assigned_to": "",
            "description": "",
            "beacons-TOTAL_FORMS": "1",
            "beacons-INITIAL_FORMS": "0",
            "beacons-MIN_NUM_FORMS": "0",
            "beacons-MAX_NUM_FORMS": "1000",
            "beacons-0-beacon_number": "B1",
            "beacons-0-northing": "100.0000",
            "beacons-0-easting": "200.0000",
            "beacons-0-height": "5.0000",
        }
        data.update(overrides)
        return data

    def test_create_job_with_beacon(self):
        response = self.client.post(reverse("jobs:create"), self._base_post_data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Job.objects.count(), 1)
        job = Job.objects.first()
        self.assertEqual(job.survey_points.count(), 1)

    def test_create_job_without_beacon_succeeds(self):
        data = self._base_post_data(**{
            "beacons-TOTAL_FORMS": "0",
        })
        response = self.client.post(reverse("jobs:create"), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Job.objects.count(), 1)
        self.assertEqual(Job.objects.first().survey_points.count(), 0)

    def test_new_job_form_has_a_single_final_create_action(self):
        response = self.client.get(reverse("jobs:create"))
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'name="coordinate_file"')
        self.assertContains(response, 'name="plan_copy"')
        self.assertContains(response, "Create Job")
        self.assertNotContains(response, "Save job and batch import coordinates")
        self.assertEqual(Job.objects.count(), 0)

    def test_create_job_imports_coordinates_and_documents_on_final_submit(self):
        with tempfile.TemporaryDirectory() as storage_root, override_settings(OBJECT_STORE_ROOT=storage_root):
            reset_store()
            try:
                response = self.client.post(
                    reverse("jobs:create"),
                    {
                        **self._base_post_data(**{"beacons-TOTAL_FORMS": "0"}),
                        "coordinate_file": SimpleUploadedFile(
                            "coordinates.csv", b"B2,200.0,100.0,5.0", content_type="text/csv"
                        ),
                        "plan_copy": SimpleUploadedFile(
                            "plan.pdf", b"%PDF-1.7\nplan content", content_type="application/pdf"
                        ),
                    },
                    follow=True,
                )

                job = Job.objects.get(plan_number="OD/1001/2026/001")
                self.assertContains(response, "created successfully")
                self.assertEqual(SurveyPoint.objects.filter(job=job).count(), 1)
                self.assertEqual(Document.objects.filter(job=job).count(), 1)
            finally:
                reset_store()

    def test_invalid_document_does_not_create_job(self):
        response = self.client.post(
            reverse("jobs:create"),
            {
                **self._base_post_data(),
                "plan_copy": SimpleUploadedFile("plan.pdf", b"not a pdf", content_type="application/pdf"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FILE CONTENT DOES NOT MATCH")
        self.assertEqual(Job.objects.count(), 0)

    def test_job_list_view(self):
        response = self.client.post(reverse("jobs:create"), self._base_post_data())
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse("jobs:list"))
        self.assertContains(response, "Warri boundary survey")
        self.assertContains(response, "Survey Type")
        self.assertContains(response, "Job Type")
        self.assertNotContains(response, "Export Selected (Word)")

    def test_job_list_search_matches_job_address(self):
        self.client.post(reverse("jobs:create"), self._base_post_data())

        response = self.client.get(reverse("jobs:list"), {"show": "active", "search": "Airport Road"})

        self.assertContains(response, "Warri boundary survey")
        response = self.client.get(reverse("jobs:list"), {"show": "active", "search": "no matching job"})
        self.assertNotContains(response, "Warri boundary survey")

    def test_job_detail_has_separate_manual_and_batch_coordinate_actions(self):
        self.client.post(reverse("jobs:create"), self._base_post_data())
        job = Job.objects.get(plan_number="OD/1001/2026/001")

        response = self.client.get(reverse("jobs:detail", args=[job.pk]))

        self.assertContains(response, "Job date:")
        self.assertNotContains(response, "Completion date")
        self.assertNotContains(response, "Job date (start)")
        self.assertContains(response, "Manual coordinate entry")
        self.assertContains(response, "Batch coordinate import")
        self.assertContains(
            response,
            reverse("survey_points:create", args=[job.pk]) + "?mode=batch",
        )

    def test_job_form_has_one_job_date_field(self):
        response = self.client.get(reverse("jobs:create"))

        self.assertContains(response, "Job date")
        self.assertNotContains(response, "Completion date")
        self.assertNotContains(response, "Job date (start)")

    def test_job_form_renders_all_workflow_fields(self):
        response = self.client.get(reverse("jobs:create"))

        self.assertContains(response, 'name="status"')
        self.assertContains(response, 'name="assigned_to"')
        self.assertContains(response, 'name="description"')

    def test_batch_coordinate_action_opens_batch_import_tab(self):
        self.client.post(reverse("jobs:create"), self._base_post_data())
        job = Job.objects.get(plan_number="OD/1001/2026/001")

        response = self.client.get(
            reverse("survey_points:create", args=[job.pk]),
            {"mode": "batch"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-entry-tab="batch" role="tab" aria-selected="true"')
        self.assertContains(response, 'data-entry-panel="manual" hidden')
