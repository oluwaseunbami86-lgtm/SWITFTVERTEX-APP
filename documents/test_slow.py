"""Slow checks: run with SV_SLOW=1 python manage.py test documents.test_slow
(large files, and genuinely simultaneous uploads - the latter needs PostgreSQL)."""
import hashlib
import os
import tempfile
import threading
import time
import unittest

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from accounts.models import Profile
from jobs.models import Job
from survey_points.models import SurveyPoint

from . import services
from .models import Document, DocumentVersion
from .storage import get_store, reset_store
from .validators import inspect_upload

SLOW = os.environ.get("SV_SLOW") == "1"
PDF = b"%PDF-1.7\n" + b"x" * 500


def make_job():
    u = User.objects.create_user("sur", password="pw-12345-x")
    u.profile.role = Profile.Role.SURVEYOR
    u.profile.status = Profile.Status.APPROVED
    u.profile.save()
    job = Job.objects.create(job_type="boundary", job_date="2026-03-02", title="J", job_address="x", survey_area="5.000",
                             area_unit="sqm", plan_number="OD/1379/2026/041", created_by=u)
    SurveyPoint.objects.create(job=job, beacon_number="B1", northing="1.0000", easting="2.0000", height="3.0000")
    job.refresh_from_db()
    return u, job


@unittest.skipUnless(SLOW, "set SV_SLOW=1")
class LargeFileTests(TransactionTestCase):
    SIZE_MB = int(os.environ.get("SV_LARGE_MB", "150"))

    def _run(self):
        u, job = make_job()
        self.client.login(username="sur", password="pw-12345-x")
        digest, tmp = hashlib.sha256(), tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        head = b"PK\x03\x04" + os.urandom(1020)
        tmp.write(head); digest.update(head)
        for _ in range(self.SIZE_MB):
            chunk = os.urandom(1024 * 1024); tmp.write(chunk); digest.update(chunk)
        tmp.close()
        try:
            with open(tmp.name, "rb") as fh:
                start = time.time()
                resp = self.client.post(reverse("documents:upload", args=[job.pk]),
                                        {"file": fh, "doc_type": "field_data", "change_note": "", "title": "", "description": "",
                                         "conflict_choice": ""}, follow=True)
            took = time.time() - start
        finally:
            os.unlink(tmp.name)
        self.assertContains(resp, "stored and verified")
        ver = DocumentVersion.objects.get()
        self.assertEqual((ver.sha256, ver.size_bytes), (digest.hexdigest(), 1024 + self.SIZE_MB * 1024 * 1024))
        self.assertEqual(get_store().stat(ver.storage_key), ver.size_bytes)
        self.assertEqual(get_store().sha256(ver.storage_key), digest.hexdigest())
        print(f"\n  {self.SIZE_MB} MB uploaded, stored and re-verified in {took:.1f}s ({type(get_store()).__name__})")

    def test_large_file_local_store(self):
        with tempfile.TemporaryDirectory() as d, override_settings(USE_S3=False, OBJECT_STORE_ROOT=d, MAX_UPLOAD_MB=500):
            reset_store(); self._run(); reset_store()

    def test_large_file_s3_api_store(self):
        import boto3
        from moto.server import ThreadedMotoServer
        server = ThreadedMotoServer(port=5078, verbose=False); server.start()
        try:
            boto3.client("s3", endpoint_url="http://127.0.0.1:5078", aws_access_key_id="k", aws_secret_access_key="s",
                         region_name="us-east-1").create_bucket(Bucket="big")
            with override_settings(USE_S3=True, AWS_ACCESS_KEY_ID="k", AWS_SECRET_ACCESS_KEY="s", AWS_STORAGE_BUCKET_NAME="big",
                                   AWS_S3_ENDPOINT_URL="http://127.0.0.1:5078", AWS_S3_REGION_NAME="us-east-1", MAX_UPLOAD_MB=500):
                reset_store(); self._run(); reset_store()
        finally:
            server.stop()


@unittest.skipUnless(SLOW and connection.vendor == "postgresql", "needs SV_SLOW=1 and PostgreSQL")
class SimultaneousUploadTests(TransactionTestCase):
    def test_parallel_new_versions_get_distinct_numbers_and_one_current(self):
        u, job = make_job()
        with tempfile.TemporaryDirectory() as d, override_settings(USE_S3=False, OBJECT_STORE_ROOT=d):
            reset_store()
            first = SimpleUploadedFile("Plan.pdf", PDF)
            doc, _ = services.add_document(job=job, uploaded=first, info=inspect_upload(first), doc_type="pdf_report",
                                           title="", description="", note="", user=u)
            errors, N = [], 8
            barrier = threading.Barrier(N)

            def worker(i):
                try:
                    content = b"%PDF-1.7\n" + f"revision {i}".encode() * 40
                    f = SimpleUploadedFile("Plan.pdf", content)
                    info = inspect_upload(f)
                    barrier.wait()
                    services.add_version(document_pk=doc.pk, uploaded=f, info=info, note=f"r{i}", user=u)
                except Exception as exc:
                    errors.append(repr(exc))
                finally:
                    connections.close_all()

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
            [t.start() for t in threads]; [t.join() for t in threads]
            self.assertEqual(errors, [])
            numbers = sorted(DocumentVersion.objects.filter(document=doc).values_list("version_number", flat=True))
            self.assertEqual(numbers, list(range(1, N + 2)))                       # no gaps, no duplicates
            self.assertEqual(DocumentVersion.objects.filter(document=doc, is_current=True).count(), 1)
            self.assertEqual(len(dict(get_store().list(""))), N + 1)              # every file really stored
            reset_store()

    def test_parallel_job_edits_only_one_wins(self):
        u, job = make_job()
        self.client.login(username="sur", password="pw-12345-x")
        results, barrier = [], threading.Barrier(2)

        def worker(i):
            from django.test import Client
            c = Client(); c.login(username="sur", password="pw-12345-x")
            data = {"job_type": "boundary", "job_date": "2026-03-02", "title": f"edit {i}", "client": "",
                    "project": "", "job_address": "x", "survey_area": "5.000", "area_unit": "sqm", "plan_number": "OD/1379/2026/041",
                    "has_reference_number": "", "reference_number": "", "status": "assigned", "assigned_to": "", "description": "",
                    "expected_version": "1", "beacons-TOTAL_FORMS": "0", "beacons-INITIAL_FORMS": "0",
                    "beacons-MIN_NUM_FORMS": "0", "beacons-MAX_NUM_FORMS": "1000"}
            sp = SurveyPoint.objects.get(job=job)
            data.update({"beacons-TOTAL_FORMS": "1", "beacons-INITIAL_FORMS": "1", "beacons-0-id": sp.pk, "beacons-0-job": job.pk,
                         "beacons-0-beacon_number": "B1", "beacons-0-northing": "1.0000", "beacons-0-easting": "2.0000", "beacons-0-height": "3.0000"})
            barrier.wait()
            r = c.post(reverse("jobs:update", args=[job.pk]), data)
            results.append((i, r.status_code))
            connections.close_all()

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        [t.start() for t in ts]; [t.join() for t in ts]
        job.refresh_from_db()
        self.assertEqual(job.row_version, 2, results)                              # exactly one save went through
        self.assertEqual(sorted(code for _, code in results), [200, 302], results)  # the other was refused, not silently merged
