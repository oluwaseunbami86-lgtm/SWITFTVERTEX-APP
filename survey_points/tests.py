import json

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from clients.models import Client
from jobs.models import Job

from .models import SurveyPoint
from .services import (
    MAX_IMPORT_BYTES,
    build_import_records,
    parse_coordinate_file,
    save_survey_point_records,
    suggest_header_mapping,
    validate_survey_point_records,
)


class SurveyPointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="surveyor1", password="testpass123")
        from accounts.models import Profile
        self.user.profile.role = Profile.Role.SURVEYOR
        self.user.profile.status = Profile.Status.APPROVED
        self.user.profile.save()
        self.client.login(username="surveyor1", password="testpass123")
        client_obj = Client.objects.create(name="Acme")
        self.job = Job.objects.create(
            job_type="boundary",
            job_date="2026-01-15",
            title="Job A",
            client=client_obj,
            job_address="1 Test Street",
            survey_area="250.000",
            area_unit="sqm",
            plan_number="OD/2002/2026/001",
            created_by=self.user,
        )

    def test_create_survey_point(self):
        response = self.client.post(reverse("survey_points:create", args=[self.job.pk]), {
            "job": self.job.pk,
            "beacon_number": "BP1234X",
            "northing": "5.5123",
            "easting": "5.7500",
            "height": "10.0000",
            "notes": "",
        })
        self.assertEqual(SurveyPoint.objects.count(), 1)
        self.assertEqual(response.status_code, 302)

    def test_manual_entry_saves_multiple_rows(self):
        response = self.client.post(reverse("survey_points:create", args=[self.job.pk]), {
            "action": "manual",
            "points-TOTAL_FORMS": "2",
            "points-INITIAL_FORMS": "0",
            "points-MIN_NUM_FORMS": "0",
            "points-MAX_NUM_FORMS": "500",
            "points-0-beacon_number": "P1",
            "points-0-easting": "512345.678",
            "points-0-northing": "723456.123",
            "points-0-height": "45.600",
            "points-1-beacon_number": "P2",
            "points-1-easting": "512355.678",
            "points-1-northing": "723466.123",
            "points-1-height": "46.200",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.job.survey_points.count(), 2)

    def test_file_selection_displays_mapping_step(self):
        uploaded = SimpleUploadedFile(
            "points.csv",
            b"Pillar,Plan Number,Easting,Northing,Height\nP1,OD/2002/2026/001,1,2,3\n",
            content_type="text/csv",
        )
        response = self.client.post(
            reverse("survey_points:create", args=[self.job.pk]),
            {"action": "upload", "coordinate_file": uploaded},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Map fields and preview")
        self.assertContains(response, "Detected column")
        self.assertEqual(response.context["phase"], "mapping")

    def test_import_confirmation_requires_a_staged_file_and_preview(self):
        response = self.client.post(
            reverse("survey_points:create", args=[self.job.pk]),
            {
                "action": "confirm_import",
                "records_json": json.dumps([{
                    "beacon_number": "P1",
                    "plan_number": self.job.plan_number,
                    "easting": "1",
                    "northing": "2",
                    "height": "3",
                }]),
            },
        )
        self.assertEqual(response.context["phase"], "entry")
        self.assertEqual(self.job.survey_points.count(), 0)

    def test_header_csv_maps_and_imports_only_after_confirmation(self):
        upload_url = reverse("survey_points:create", args=[self.job.pk])
        uploaded = SimpleUploadedFile(
            "points.csv",
            (
                "Pillar,Plan Number,Easting,Northing,Height\n"
                f"P1,{self.job.plan_number},512345.678,723456.123,45.600\n"
            ).encode(),
            content_type="text/csv",
        )
        self.client.post(upload_url, {"action": "upload", "coordinate_file": uploaded})
        self.assertEqual(self.job.survey_points.count(), 0)
        configuration = {
            "layout": "columns",
            "has_header": True,
            "mapping": {
                "beacon_number": 0,
                "plan_number": 1,
                "easting": 2,
                "northing": 3,
                "height": 4,
            },
        }
        response = self.client.post(upload_url, {
            "action": "preview",
            "configuration": json.dumps(configuration),
        })
        self.assertEqual(response.context["phase"], "preview")
        self.assertEqual(response.context["import_errors"], [])
        self.assertEqual(response.context["valid_count"], 1)
        self.assertEqual(self.job.survey_points.count(), 0)

        response = self.client.post(upload_url, {
            "action": "confirm_import",
            "records_json": json.dumps([{
                "beacon_number": "P1",
                "plan_number": self.job.plan_number,
                "easting": "512345.678",
                "northing": "723456.123",
                "height": "45.600",
            }]),
        })
        self.assertEqual(response.context["phase"], "results")
        self.assertEqual(self.job.survey_points.count(), 1)

    def test_csv_without_header_and_comma_txt_parse_as_columns(self):
        for name, content in (
            ("points.csv", b"P1,OD/2002/2026/001,1,2,3\n"),
            ("points.txt", b"P2,OD/2002/2026/001,4,5,6\n"),
        ):
            parsed = parse_coordinate_file(SimpleUploadedFile(name, content))
            configuration = {
                "layout": "columns",
                "has_header": False,
                "mapping": {
                    "beacon_number": 0,
                    "plan_number": 1,
                    "easting": 2,
                    "northing": 3,
                    "height": 4,
                },
            }
            records, issues = build_import_records(parsed, configuration, self.job.plan_number)
            self.assertEqual(issues, [])
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["plan_number"], "OD/2002/2026/001")

    def test_vertical_text_mapping_handles_records_without_plan_number(self):
        parsed = parse_coordinate_file(SimpleUploadedFile(
            "points.txt",
            b"P1\n512345.678\n723456.123\n45.600\nP2\n512355.678\n723466.123\n46.200\n",
        ))
        configuration = {
            "layout": "vertical",
            "lines_per_record": 4,
            "mapping": {
                "beacon_number": 0,
                "easting": 1,
                "northing": 2,
                "height": 3,
            },
        }
        records, issues = build_import_records(parsed, configuration, self.job.plan_number)
        self.assertEqual(issues, [])
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["beacon_number"], "P2")
        self.assertEqual(records[0]["plan_number"], self.job.plan_number)

    def test_validation_reports_missing_invalid_and_duplicate_values(self):
        rows = [
            {"beacon_number": "P1", "easting": "not-number", "northing": "", "height": "4"},
            {"beacon_number": "P1", "easting": "1", "northing": "2", "height": "3"},
        ]
        _, issues = validate_survey_point_records(self.job, rows)
        self.assertTrue(any(issue["field"] == "easting" and "valid number" in issue["message"] for issue in issues))
        self.assertTrue(any(issue["field"] == "northing" and "required" in issue["message"] for issue in issues))
        self.assertTrue(any("Duplicate" in issue["message"] for issue in issues))

    def test_empty_csv_record_is_reported_instead_of_skipped(self):
        parsed = parse_coordinate_file(SimpleUploadedFile(
            "points.csv",
            b"Pillar,Easting,Northing,Height\nP1,1,2,3\n,,,\n",
            content_type="text/csv",
        ))
        records, structural_issues = build_import_records(parsed, {
            "layout": "columns",
            "has_header": True,
            "mapping": {
                "beacon_number": 0,
                "easting": 1,
                "northing": 2,
                "height": 3,
            },
        }, self.job.plan_number)
        _, validation_issues = validate_survey_point_records(
            self.job, records, captured_by=self.user
        )
        self.assertEqual(structural_issues, [])
        self.assertEqual(len(records), 2)
        self.assertTrue(any(issue["row"] == 2 for issue in validation_issues))

    def test_file_size_limit_is_enforced(self):
        with self.assertRaisesMessage(ValueError, "4 MB or smaller"):
            parse_coordinate_file(SimpleUploadedFile(
                "large.csv",
                b"x" * (MAX_IMPORT_BYTES + 1),
                content_type="text/csv",
            ))

    def test_large_coordinate_batch_is_saved_in_one_safe_operation(self):
        rows = [{
            "beacon_number": f"BP-{index:05}",
            "plan_number": self.job.plan_number,
            "easting": "512345.678",
            "northing": "723456.123",
            "height": "45.600",
        } for index in range(1200)]
        points, issues = save_survey_point_records(
            job=self.job, user=self.user, rows=rows
        )
        self.assertEqual(issues, [])
        self.assertEqual(len(points), 1200)
        self.assertEqual(self.job.survey_points.count(), 1200)

    def test_viewer_cannot_upload_or_import_coordinates(self):
        viewer = User.objects.create_user(username="viewer", password="test-password")
        from accounts.models import Profile
        viewer.profile.role = Profile.Role.CLIENT_VIEWER
        viewer.profile.status = Profile.Status.APPROVED
        viewer.profile.save()
        self.client.force_login(viewer)
        response = self.client.post(
            reverse("survey_points:create", args=[self.job.pk]),
            {"action": "upload"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.job.survey_points.count(), 0)

    def test_existing_duplicates_block_atomic_import(self):
        SurveyPoint.objects.create(
            job=self.job,
            beacon_number="P1",
            easting="1",
            northing="2",
            height="3",
        )
        points, issues = save_survey_point_records(
            job=self.job,
            user=self.user,
            rows=[{
                "beacon_number": "p1",
                "plan_number": self.job.plan_number,
                "easting": "4",
                "northing": "5",
                "height": "6",
            }],
        )
        self.assertEqual(points, [])
        self.assertTrue(any("already exists" in issue["message"] for issue in issues))
        self.assertEqual(self.job.survey_points.count(), 1)

    def test_invalid_record_blocks_otherwise_valid_rows_from_saving(self):
        points, issues = save_survey_point_records(
            job=self.job,
            user=self.user,
            rows=[
                {
                    "beacon_number": "P1",
                    "plan_number": self.job.plan_number,
                    "easting": "1",
                    "northing": "2",
                    "height": "3",
                },
                {
                    "beacon_number": "P2",
                    "plan_number": self.job.plan_number,
                    "easting": "bad",
                    "northing": "2",
                    "height": "3",
                },
            ],
        )
        self.assertEqual(points, [])
        self.assertTrue(issues)
        self.assertEqual(self.job.survey_points.count(), 0)

    def test_header_suggestions_detect_survey_fields(self):
        mapping = suggest_header_mapping(["Pillar Number", "Plan Number", "Easting", "Northing", "Height"])
        self.assertEqual(mapping, {
            "beacon_number": 0,
            "plan_number": 1,
            "easting": 2,
            "northing": 3,
            "height": 4,
        })
