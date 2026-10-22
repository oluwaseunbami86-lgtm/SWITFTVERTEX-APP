import hashlib
import io
import os
import tempfile
import urllib.request
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile
from audit.models import AuditEvent
from clients.models import Client
from jobs.models import Job
from survey_points.models import SurveyPoint

from . import services
from .models import Document, DocumentVersion, ReplacedFile
from .storage import StorageError, get_store, reset_store

PW = "pw-12345-x"
PDF = b"%PDF-1.7\n" + b"survey report content " * 50
PDF2 = b"%PDF-1.7\n" + b"REVISED survey report " * 60
DWG = b"AC1021\x00\x00\x00\x00\x00\x01\x03\x00" + os.urandom(2000)
DWG2 = b"AC1021\x00\x00\x00\x00\x00\x01\x03\x00" + os.urandom(3000)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def mkuser(name, role, superuser=False):
    u = User.objects.create_user(username=name, password=PW, is_superuser=superuser)
    u.profile.role = role
    u.profile.status = Profile.Status.APPROVED
    u.profile.save()
    return u


class StoreCases:
    """Every behaviour below must hold on EVERY object store backend."""

    def setUp(self):
        self.admin = mkuser("adm", "admin")
        self.surveyor = mkuser("sur", "surveyor")
        self.tech = mkuser("tec", "surveyor")
        self.viewer = mkuser("vwr", Profile.Role.CLIENT_VIEWER)
        client = Client.objects.create(name="Archive client", created_by=self.admin)
        self.viewer.profile.client = client
        self.viewer.profile.save()
        self.job = Job.objects.create(job_type="boundary", job_date="2026-03-02", title="J", job_address="x",
                                      survey_area="5.000", area_unit="sqm", plan_number="OD/1379/2026/041",
                                      created_by=self.surveyor, assigned_to=self.tech, client=client)
        SurveyPoint.objects.create(job=self.job, beacon_number="B1", northing="1.0000", easting="2.0000", height="3.0000")
        self.login(self.surveyor)

    def login(self, user):
        self.client.logout()
        self.assertTrue(self.client.login(username=user.username, password=PW))

    def upload(self, name, content, choice="", note="", doc_type="drawing", document=None, job=None):
        data = {"file": SimpleUploadedFile(name, content), "doc_type": doc_type, "title": "", "description": "",
                "change_note": note, "conflict_choice": choice}
        if document:
            data["document"] = document
        return self.client.post(reverse("documents:upload", args=[(job or self.job).pk]), data, follow=True)

    def stored(self):
        return dict(get_store().list(""))

    def read(self, key):
        store = get_store()
        return store.sha256(key)

    # ---- happy path ---------------------------------------------------------------------------
    def test_upload_creates_verified_version_one_in_expected_place(self):
        resp = self.upload("Survey_Plan.dwg", DWG)
        self.assertContains(resp, "stored and verified")
        ver = DocumentVersion.objects.get()
        self.assertEqual((ver.version_number, ver.is_current, ver.sha256, ver.size_bytes), (1, True, sha(DWG), len(DWG)))
        self.assertTrue(ver.storage_key.startswith("jobs/2026/OD/1379/2026/041/cad/"))
        self.assertEqual(self.stored()[ver.storage_key], len(DWG))
        self.assertEqual(self.read(ver.storage_key), sha(DWG))
        self.assertTrue(AuditEvent.objects.filter(action="document_uploaded").exists())

    def test_unsupported_and_fake_files_are_refused_and_nothing_is_stored(self):
        for name, content, msg in (("run.exe", b"MZ\x90\x00", "FILE TYPE NOT SUPPORTED"),
                                   ("plan.pdf", b"this is not a pdf", "DOES NOT MATCH ITS .PDF"),
                                   ("empty.pdf", b"", "EMPTY"), ("noext", b"abc", "FILE TYPE NOT SUPPORTED")):
            resp = self.upload(name, content)
            self.assertContains(resp, "empty" if "EMPTY" in msg else msg.split(" - ")[0], msg_prefix=name)
        self.assertEqual((Document.objects.count(), DocumentVersion.objects.count(), self.stored()), (0, 0, {}))
        self.assertTrue(AuditEvent.objects.filter(action="upload_failed").exists())

    def test_too_large_is_refused(self):
        with override_settings(MAX_UPLOAD_MB=0):
            self.assertContains(self.upload("a.pdf", PDF), "FILE TOO LARGE")
        self.assertEqual(self.stored(), {})

    def test_identical_file_is_refused(self):
        self.upload("a.pdf", PDF, doc_type="pdf_report")
        self.assertContains(self.upload("copy.pdf", PDF, doc_type="pdf_report"), "THIS EXACT FILE IS ALREADY STORED")
        self.assertEqual(DocumentVersion.objects.count(), 1)

    # ---- versioning and the overwrite question --------------------------------------------------
    def test_same_name_without_an_answer_is_refused_loudly(self):
        self.upload("Plan.pdf", PDF, doc_type="pdf_report")
        resp = self.upload("plan.PDF", PDF2, doc_type="pdf_report")        # different case, same name
        self.assertContains(resp, "ALREADY EXISTS IN THIS JOB")
        self.assertEqual((DocumentVersion.objects.count(), len(self.stored())), (1, 1))

    def test_not_granting_overwrite_creates_a_new_version_and_keeps_both(self):
        self.upload("Plan.pdf", PDF, doc_type="pdf_report")
        self.upload("Plan.pdf", PDF2, choice="new_version", note="added road")
        v = list(DocumentVersion.objects.order_by("version_number"))
        self.assertEqual([(x.version_number, x.is_current) for x in v], [(1, False), (2, True)])
        self.assertEqual(v[1].change_note, "added road")
        self.assertEqual(self.read(v[0].storage_key), sha(PDF))
        self.assertEqual(self.read(v[1].storage_key), sha(PDF2))
        self.assertEqual(Document.objects.count(), 1)

    def test_granted_overwrite_replaces_current_but_keeps_old_bytes_for_retention(self):
        self.upload("Plan.pdf", PDF, doc_type="pdf_report")
        old_key = DocumentVersion.objects.get().storage_key
        resp = self.upload("Plan.pdf", PDF2, choice="overwrite")
        self.assertContains(resp, "Overwrote the current file")
        ver = DocumentVersion.objects.get()
        self.assertEqual((ver.version_number, ver.sha256), (1, sha(PDF2)))
        self.assertNotEqual(ver.storage_key, old_key)
        rep = ReplacedFile.objects.get()
        self.assertEqual((rep.storage_key, rep.sha256), (old_key, sha(PDF)))
        self.assertEqual(self.read(old_key), sha(PDF))                      # old content still recoverable
        self.assertTrue(rep.purge_after > timezone.now())
        ev = AuditEvent.objects.get(action="document_overwritten")
        self.assertEqual((ev.old_value["sha256"], ev.new_value["sha256"]), (sha(PDF), sha(PDF2)))

    def test_expired_replaced_files_are_purged_but_unexpired_are_kept(self):
        self.upload("Plan.pdf", PDF, doc_type="pdf_report")
        self.upload("Plan.pdf", PDF2, choice="overwrite")
        rep = ReplacedFile.objects.get()
        call_command("purge_replaced_files")
        self.assertIn(rep.storage_key, self.stored())
        ReplacedFile.objects.update(purge_after=timezone.now() - timezone.timedelta(days=1))
        call_command("purge_replaced_files")
        self.assertNotIn(rep.storage_key, self.stored())
        self.assertIsNotNone(ReplacedFile.objects.get().purged_at)

    def test_assigned_surveyor_can_add_and_overwrite_document_versions(self):
        self.upload("Plan.pdf", PDF, doc_type="pdf_report")
        self.login(self.tech)
        self.upload("Plan.pdf", PDF2, choice="new_version")
        self.assertEqual(DocumentVersion.objects.count(), 2)
        revised_pdf = PDF2 + b"final overwrite"
        self.assertContains(self.upload("Plan.pdf", revised_pdf, choice="overwrite"), "Overwrote the current file")
        self.assertEqual(DocumentVersion.objects.count(), 2)

    def test_old_version_can_be_made_current_and_stays_downloadable(self):
        self.upload("Plan.pdf", PDF, doc_type="pdf_report")
        self.upload("Plan.pdf", PDF2, choice="new_version")
        v1 = DocumentVersion.objects.get(version_number=1)
        self.client.post(reverse("documents:make_current", args=[v1.pk]))
        self.assertEqual(list(DocumentVersion.objects.filter(is_current=True).values_list("version_number", flat=True)), [1])
        self.assertEqual(self.download(v1), PDF)

    def test_database_allows_only_one_current_version(self):
        self.upload("Plan.pdf", PDF, doc_type="pdf_report")
        self.upload("Plan.pdf", PDF2, choice="new_version")
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            DocumentVersion.objects.filter(version_number=1).update(is_current=True)

    # ---- download & access ---------------------------------------------------------------------
    def download(self, ver):
        resp = self.client.get(reverse("documents:download", args=[ver.pk]))
        if resp.status_code == 302 and resp["Location"].startswith("http"):
            with urllib.request.urlopen(resp["Location"]) as r:
                return r.read()
        return b"".join(resp.streaming_content)

    def test_download_returns_exact_bytes_and_is_audited(self):
        self.upload("Survey_Plan.dwg", DWG)
        ver = DocumentVersion.objects.get()
        self.login(self.viewer)
        self.assertEqual(self.download(ver), DWG)
        self.assertTrue(AuditEvent.objects.filter(action="document_downloaded", username="vwr").exists())

    def test_anonymous_cannot_download_or_see_documents(self):
        self.upload("Survey_Plan.dwg", DWG)
        ver = DocumentVersion.objects.get()
        self.client.logout()
        for url in (reverse("documents:download", args=[ver.pk]), reverse("documents:detail", args=[ver.document_id])):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302)
            self.assertIn("/sign-in/", resp["Location"])

    def test_viewer_cannot_upload_or_change_documents(self):
        self.upload("a.pdf", PDF, doc_type="pdf_report")
        doc = Document.objects.get()
        self.login(self.viewer)
        self.assertEqual(self.client.get(reverse("documents:upload", args=[self.job.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("documents:delete", args=[doc.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("documents:make_current", args=[doc.versions.get().pk])).status_code, 403)

    def test_other_surveyors_cannot_add_to_a_job_they_do_not_own(self):
        other = mkuser("oth", "surveyor")
        self.login(other)
        self.assertEqual(self.client.get(reverse("documents:upload", args=[self.job.pk])).status_code, 403)

    # ---- integrity -----------------------------------------------------------------------------
    def test_missing_object_blocks_download_and_is_reported(self):
        self.upload("Survey_Plan.dwg", DWG)
        ver = DocumentVersion.objects.get()
        get_store().delete(ver.storage_key)
        resp = self.client.get(reverse("documents:download", args=[ver.pk]), follow=True)
        self.assertContains(resp, "MISSING OR DAMAGED")
        ver.refresh_from_db()
        self.assertEqual(ver.integrity_status, "missing")
        self.assertTrue(AuditEvent.objects.filter(action="file_integrity_failed").exists())

    def test_tampered_object_is_detected_by_verification(self):
        self.upload("Survey_Plan.dwg", DWG)
        ver = DocumentVersion.objects.get()
        self.tamper(ver.storage_key, DWG[:-1] + b"X")                         # same size, different bytes
        resp = self.client.post(reverse("documents:verify", args=[ver.document_id]), follow=True)
        self.assertContains(resp, "INTEGRITY CHECK FAILED")
        ver.refresh_from_db()
        self.assertEqual(ver.integrity_status, "corrupt")
        with self.assertRaises(SystemExit):
            call_command("verify_files")

    def test_verify_files_passes_on_healthy_archive(self):
        self.upload("Survey_Plan.dwg", DWG)
        call_command("verify_files")
        self.assertEqual(DocumentVersion.objects.get().integrity_status, "ok")

    def test_reconcile_finds_orphans_and_missing(self):
        self.upload("Survey_Plan.dwg", DWG)
        get_store().put(io.BytesIO(b"x"), "jobs/2026/OD/1/2026/001/cad/stray.pdf", size=1, sha256=sha(b"x"), content_type="application/pdf")
        out = io.StringIO()
        call_command("reconcile_storage", stdout=out)
        self.assertIn("ORPHAN", out.getvalue())
        get_store().delete(DocumentVersion.objects.get().storage_key)
        with self.assertRaises(SystemExit):
            call_command("reconcile_storage", stderr=io.StringIO(), stdout=io.StringIO())

    # ---- failure: nothing false is ever recorded -------------------------------------------------
    def test_storage_failure_during_upload_is_loud_and_leaves_no_record(self):
        with mock.patch.object(type(get_store()), "put", side_effect=StorageError("boom")):
            resp = self.upload("Survey_Plan.dwg", DWG)
        self.assertContains(resp, "FILE UPLOAD FAILED - THE FILE WAS NOT SUCCESSFULLY STORED")
        self.assertNotContains(resp, "stored and verified")
        self.assertEqual((Document.objects.count(), DocumentVersion.objects.count(), self.stored()), (0, 0, {}))
        self.assertTrue(AuditEvent.objects.filter(action="upload_failed").exists())

    def test_database_failure_after_storing_removes_the_object_again(self):
        with mock.patch("documents.services.log_event", side_effect=RuntimeError("db down")):
            resp = self.upload("Survey_Plan.dwg", DWG)
        self.assertContains(resp, "FILE UPLOAD FAILED")
        self.assertEqual((Document.objects.count(), DocumentVersion.objects.count(), self.stored()), (0, 0, {}))

    def test_corrupted_in_transit_is_caught_before_saving(self):
        real_put = type(get_store()).put

        def bad_put(self_, fileobj, key, **kw):
            real_put(self_, io.BytesIO(DWG[:-1] + b"X"), key, **kw)          # stored bytes differ from uploaded bytes

        with mock.patch.object(type(get_store()), "put", bad_put):
            resp = self.upload("Survey_Plan.dwg", DWG)
        self.assertContains(resp, "FILE UPLOAD FAILED")
        self.assertEqual((DocumentVersion.objects.count(), self.stored()), (0, {}))

    # ---- delete protection ---------------------------------------------------------------------
    def test_document_delete_restore_and_typed_purge(self):
        self.upload("Plan.pdf", PDF, doc_type="pdf_report")
        self.upload("Plan.pdf", PDF2, choice="new_version")
        doc = Document.objects.get()
        keys = list(doc.versions.values_list("storage_key", flat=True))
        self.login(self.surveyor)
        self.assertEqual(self.client.post(reverse("documents:delete", args=[doc.pk])).status_code, 403)
        self.login(self.admin)
        self.client.post(reverse("documents:purge", args=[doc.pk]), {"confirm_text": "DELETE Plan.pdf"})
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())          # not deleted first -> refused
        self.client.post(reverse("documents:delete", args=[doc.pk]))
        doc.refresh_from_db()
        self.assertEqual(doc.lifecycle, "deleted")
        self.assertTrue(all(k in self.stored() for k in keys))                # soft delete keeps every file
        self.client.post(reverse("documents:restore", args=[doc.pk]))
        self.client.post(reverse("documents:delete", args=[doc.pk]))
        self.client.post(reverse("documents:purge", args=[doc.pk]), {"confirm_text": "nope"})
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("documents:purge", args=[doc.pk]), {"confirm_text": "DELETE Plan.pdf"})
        self.assertFalse(Document.objects.filter(pk=doc.pk).exists())
        self.assertEqual(self.stored(), {})
        self.assertEqual(AuditEvent.objects.get(action="document_purged").old_value["job"], "OD/1379/2026/041")

    def test_purge_confirmation_matches_filename_as_displayed(self):
        self.upload("Plan   One.pdf", PDF, doc_type="pdf_report")
        doc = Document.objects.get()
        self.login(self.admin)
        self.client.post(reverse("documents:delete", args=[doc.pk]))

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("documents:purge", args=[doc.pk]), {"confirm_text": "DELETE Plan One.pdf"})

        self.assertFalse(Document.objects.filter(pk=doc.pk).exists())
        self.assertEqual(self.stored(), {})

    def test_purging_a_job_removes_its_stored_files(self):
        self.upload("Plan.pdf", PDF, doc_type="pdf_report")
        self.login(self.admin)
        for name in ("archive", "delete"):
            self.client.post(reverse(f"jobs:{name}", args=[self.job.pk]))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("jobs:purge", args=[self.job.pk]), {"confirm_text": "DELETE OD/1379/2026/041"})
        self.assertFalse(Job.all_objects.filter(pk=self.job.pk).exists())
        self.assertEqual((DocumentVersion.objects.count(), self.stored()), (0, {}))

    def test_search_finds_documents_by_filename_on_the_documents_page(self):
        self.upload("Lekki_Layout.dwg", DWG)
        self.assertContains(self.client.get(reverse("documents:list") + "?q=lekki"), "Lekki_Layout.dwg")
        self.assertNotContains(self.client.get(reverse("documents:list") + "?q=zzz"), "Lekki_Layout.dwg")

    def test_document_list_filters_and_upload_job_selection_work(self):
        self.upload("Layout.dwg", DWG, doc_type="drawing")
        self.upload("Report.pdf", PDF, doc_type="pdf_report")

        response = self.client.get(reverse("documents:list"), {"type": "drawing"})
        self.assertContains(response, "Layout.dwg")
        self.assertNotContains(response, "Report.pdf")

        response = self.client.get(reverse("documents:list"), {"version": "1"})
        self.assertContains(response, "Layout.dwg")
        self.assertContains(response, "Report.pdf")

        response = self.client.get(reverse("documents:list"), {"upload_job": self.job.pk})
        self.assertRedirects(
            response,
            reverse("documents:upload", args=[self.job.pk]),
            fetch_redirect_response=False,
        )

    def tamper(self, key, content):
        raise NotImplementedError


class LocalStoreTests(StoreCases, TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._ov = override_settings(USE_S3=False, OBJECT_STORE_ROOT=self._tmp.name)
        self._ov.enable()
        reset_store()
        super().setUp()

    def tearDown(self):
        self._ov.disable()
        reset_store()
        self._tmp.cleanup()

    def tamper(self, key, content):
        with open(os.path.join(self._tmp.name, key), "wb") as fh:
            fh.write(content)


class R2CompatibleStoreTests(StoreCases, TestCase):
    """Same behaviours against an S3-API server (moto) - the protocol Cloudflare R2 speaks."""

    server = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from moto.server import ThreadedMotoServer
        cls.server = ThreadedMotoServer(port=5077, verbose=False)
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        super().tearDownClass()

    def setUp(self):
        import boto3
        self.s3 = boto3.client("s3", endpoint_url="http://127.0.0.1:5077", aws_access_key_id="k",
                               aws_secret_access_key="s", region_name="us-east-1")
        self.bucket = f"sv-test-{os.urandom(4).hex()}"
        self.s3.create_bucket(Bucket=self.bucket)
        self._ov = override_settings(USE_S3=True, AWS_ACCESS_KEY_ID="k", AWS_SECRET_ACCESS_KEY="s",
                                     AWS_STORAGE_BUCKET_NAME=self.bucket, AWS_S3_ENDPOINT_URL="http://127.0.0.1:5077",
                                     AWS_S3_REGION_NAME="us-east-1")
        self._ov.enable()
        reset_store()
        super().setUp()

    def tearDown(self):
        self._ov.disable()
        reset_store()

    def tamper(self, key, content):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=content)

    def test_downloads_are_short_lived_signed_urls_never_public(self):
        self.upload("Survey_Plan.dwg", DWG)
        ver = DocumentVersion.objects.get()
        resp = self.client.get(reverse("documents:download", args=[ver.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("X-Amz-Signature", resp["Location"])
        self.assertIn("X-Amz-Expires=300", resp["Location"])
        self.assertEqual(resp["Cache-Control"], "private, no-store")
        bare = resp["Location"].split("?")[0]                                  # the same URL without its signature
        with self.assertRaises(Exception):
            urllib.request.urlopen(bare)

    def test_unreachable_storage_fails_loudly(self):
        with override_settings(AWS_S3_ENDPOINT_URL="http://127.0.0.1:1"):
            reset_store()
            resp = self.upload("Survey_Plan.dwg", DWG)
        self.assertContains(resp, "FILE UPLOAD FAILED - THE FILE WAS NOT SUCCESSFULLY STORED")
        self.assertEqual((Document.objects.count(), DocumentVersion.objects.count()), (0, 0))
        reset_store()
        self.assertEqual(self.stored(), {})
