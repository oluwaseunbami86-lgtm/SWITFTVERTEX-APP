import io
import csv
import zipfile
import xml.etree.ElementTree as ET

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Profile
from clients.models import Client
from jobs.models import Job


class ReportsExportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="reporter", password="secret123")
        self.user.profile.role = Profile.Role.OFFICE_STAFF
        self.user.profile.status = Profile.Status.APPROVED
        self.user.profile.save()
        self.client.force_login(self.user)
        self.client_obj = Client.objects.create(name="Acme Estates")

        job = Job.objects.create(
            job_type="boundary",
            job_date="2026-01-15",
            title="Warri boundary survey",
            client=self.client_obj,
            job_address="12 Airport Road, Warri",
            survey_area="500.000",
            area_unit="sqm",
            plan_number="OD/1001/2026/001",
            status="assigned",
        )
        job.survey_points.create(
            beacon_number="B1",
            northing="100.0000",
            easting="200.0000",
            height="5.0000",
        )
        self.job = job

    def test_csv_export_keeps_all_beacons_in_one_job_row(self):
        self.job.survey_points.create(
            beacon_number="B2",
            northing="101.0000",
            easting="201.0000",
            height="5.1000",
        )
        response = self.client.get(reverse("reports:export_jobs_csv"), {"job_ids": str(self.job.pk)})

        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
        self.assertEqual(rows[0], [
            "PILLAR NO",
            "NORTHINGS (M)",
            "EASTINGS (M)",
            "PLAN NO: ORIGIN: REF. PLAN NO.",
            "TITLE",
            "LOCATION",
            "AREA",
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][:3], ["B1\nB2", "100.000\n101.000", "200.000\n201.000"])

    def test_docx_export_puts_every_beacon_in_one_job_table_row(self):
        self.job.survey_points.create(
            beacon_number="B2",
            northing="101.0000",
            easting="201.0000",
            height="5.1000",
        )
        response = self.client.get(reverse("reports:export_jobs_docx"), {"job_ids": str(self.job.pk)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            document = archive.read("word/document.xml").decode("utf-8")

        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        root = ET.fromstring(document)
        table_rows = root.findall(".//w:tbl/w:tr", namespace)
        self.assertEqual(len(table_rows), 2)  # Header plus one row for the job.
        self.assertIn("PILLAR NO", document)
        self.assertIn("B1", document)
        self.assertIn("B2", document)
        self.assertIn("Warri boundary survey", document)

    def test_selected_export_requires_at_least_one_job(self):
        response = self.client.get(reverse("reports:export_jobs_docx"), {"selection": "1"})

        self.assertEqual(response.status_code, 400)
