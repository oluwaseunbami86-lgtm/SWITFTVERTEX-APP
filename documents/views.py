import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from accounts import permissions as perm
from audit.models import AuditEvent
from audit.services import log_event
from clients.models import Client
from jobs.models import Job

from . import services
from .forms import UploadForm
from .models import Document, DocumentVersion
from .services import DocumentError
from .storage import StorageError, get_store
from .validators import INLINE_SAFE, UploadRejected, inspect_upload

log = logging.getLogger("swiftvertex")
L = Job.Lifecycle


def _visible_document(request, pk):
    doc = get_object_or_404(Document.objects.select_related("job", "job__client"), pk=pk)
    if not perm.can_view_job(request.user, doc.job):
        raise Http404
    if doc.job.lifecycle == L.DELETED or doc.lifecycle == Document.Lifecycle.DELETED:
        if not perm.can_delete(request.user):
            raise Http404
    return doc


@login_required
def document_list(request):
    qs = (Document.objects.filter(lifecycle=Document.Lifecycle.ACTIVE).exclude(job__lifecycle=L.DELETED)
          .filter(job__in=perm.visible_jobs(request.user))
          .select_related("job", "job__client", "uploaded_by")
          .prefetch_related(Prefetch(
              "versions",
              queryset=DocumentVersion.objects.filter(is_current=True),
              to_attr="current_versions",
          )))
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(job__plan_number__icontains=q)
                       | Q(versions__original_filename__icontains=q)).distinct()

    type_choices = dict(Document.DocType.choices)
    doc_type = request.GET.get("type", "")
    if doc_type not in type_choices:
        doc_type = ""
    if doc_type:
        qs = qs.filter(doc_type=doc_type)

    clients = Client.objects.filter(jobs__documents__in=qs).distinct()
    client_id = request.GET.get("client", "")
    if not client_id.isdecimal() or not clients.filter(pk=client_id).exists():
        client_id = ""
    if client_id:
        qs = qs.filter(job__client_id=client_id)

    versions = (DocumentVersion.objects.filter(document__in=qs, is_current=True)
                .values_list("version_number", flat=True).distinct().order_by("version_number"))
    version = request.GET.get("version", "")
    if not version.isdecimal() or not versions.filter(version_number=version).exists():
        version = ""
    if version:
        qs = qs.filter(versions__is_current=True, versions__version_number=version)

    upload_jobs = [
        job for job in Job.objects.filter(lifecycle=L.ACTIVE).select_related("client")
        if perm.can_edit_content(request.user, job)
    ]
    upload_job_id = request.GET.get("upload_job", "")
    if upload_job_id:
        upload_job = get_object_or_404(Job, pk=upload_job_id, lifecycle=L.ACTIVE)
        perm.require(perm.can_edit_content(request.user, upload_job))
        return redirect("documents:upload", job_pk=upload_job.pk)

    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    for d in page:
        d._current_cache = d.current_versions[0] if d.current_versions else False
    return render(request, "documents/document_list.html", {
        "documents": page,
        "page": page,
        "q": q,
        "type_choices": Document.DocType.choices,
        "selected_type": doc_type,
        "clients": clients,
        "selected_client": client_id,
        "versions": versions,
        "selected_version": version,
        "upload_jobs": upload_jobs,
    })


@login_required
def document_detail(request, pk):
    doc = _visible_document(request, pk)
    return render(request, "documents/document_detail.html", {
        "doc": doc, "versions": doc.versions.select_related("uploaded_by", "overwritten_by"), "job": doc.job,
    })


def _upload_log(request, job, message, exc=None):
    log.error("Upload refused/failed for job %s by %s: %s", job.pk, request.user, message, exc_info=exc)
    try:
        log_event(user=request.user, action=AuditEvent.Action.UPLOAD_FAILED, entity_type="job", entity_id=job.pk,
                  entity_label=job.plan_number, description=message[:500], request=request)
    except Exception:
        log.exception("Could not write the audit event for a failed upload")


@login_required
def document_upload(request, job_pk):
    job = get_object_or_404(Job, pk=job_pk)
    perm.require(perm.can_edit_content(request.user, job) and job.lifecycle == L.ACTIVE)
    target = None
    if request.GET.get("document") or request.POST.get("document"):
        target = get_object_or_404(Document, pk=request.GET.get("document") or request.POST.get("document"), job=job,
                                   lifecycle=Document.Lifecycle.ACTIVE)
    conflict = None
    form = UploadForm(request.POST or None, request.FILES or None, initial={"doc_type": target.doc_type if target else "other"})

    if request.method == "POST" and form.is_valid():
        data, uploaded = form.cleaned_data, form.cleaned_data["file"]
        try:
            info = inspect_upload(uploaded)
            duplicate = services.find_identical(job, info.sha256)
            if duplicate:
                raise UploadRejected(
                    f"THIS EXACT FILE IS ALREADY STORED - '{duplicate.original_filename}' version "
                    f"{duplicate.version_number} in this job. Nothing was stored.")
            same_name = None if target else services.find_same_name(job, info.filename)
            choice = data["conflict_choice"]
            if same_name and not choice:
                conflict = same_name
                raise UploadRejected(
                    f"A FILE NAMED '{info.filename}' ALREADY EXISTS IN THIS JOB - choose whether to overwrite it "
                    "or keep both as a new version, then select the file again. Nothing was stored.")
            if choice == "overwrite":
                if not perm.can_edit_job(request.user, job):
                    raise UploadRejected("YOUR ROLE CANNOT OVERWRITE FILES - choose 'new version' instead. Nothing was stored.")
                doc, ver = services.overwrite_current(document_pk=(same_name or target).pk, uploaded=uploaded, info=info,
                                                      note=data["change_note"], user=request.user, request=request)
                what = f"Overwrote the current file (previous content kept {services.settings.OVERWRITE_RETENTION_DAYS} days)."
            elif target or same_name:
                doc, ver = services.add_version(document_pk=(target or same_name).pk, uploaded=uploaded, info=info,
                                                note=data["change_note"], user=request.user, request=request)
                what = f"Saved as version {ver.version_number}."
            else:
                doc, ver = services.add_document(job=job, uploaded=uploaded, info=info, doc_type=data["doc_type"],
                                                 title=data["title"], description=data["description"],
                                                 note=data["change_note"], user=request.user, request=request)
                what = "Stored as version 1."
        except UploadRejected as exc:
            messages.error(request, str(exc))
            _upload_log(request, job, str(exc))
        except DocumentError as exc:
            messages.error(request, str(exc))
            _upload_log(request, job, str(exc))
        except IntegrityError as exc:
            messages.error(request, "RECORD WAS NOT SAVED - the file was removed from storage again. Please try once more.")
            _upload_log(request, job, "database integrity error while saving a document", exc)
        except Exception as exc:  # anything unexpected must still fail loudly, never as a 500 with a half-saved record
            messages.error(request, "FILE UPLOAD FAILED - THE FILE WAS NOT SUCCESSFULLY STORED. Nothing was saved.")
            _upload_log(request, job, "unexpected error during upload", exc)
        else:
            messages.success(request, f"'{info.filename}' stored and verified. {what}")
            return redirect("documents:detail", pk=doc.pk)

    existing = {}
    for d in Document.objects.filter(job=job, lifecycle="active"):
        cur = d.versions.filter(is_current=True).first()
        if cur:
            existing[cur.original_filename.lower()] = {"name": cur.original_filename, "version": cur.version_number}
    return render(request, "documents/document_form.html", {
        "form": form, "job": job, "target": target, "conflict": conflict, "existing": existing,
        "can_overwrite": perm.can_edit_job(request.user, job),
    })


@login_required
def version_download(request, pk):
    ver = get_object_or_404(DocumentVersion.objects.select_related("document", "document__job"), pk=pk)
    doc = _visible_document(request, ver.document_id)  # 404 for anyone not allowed to see it
    inline = request.GET.get("inline") == "1" and ver.mime_type in INLINE_SAFE
    try:
        if ver.storage_backend == DocumentVersion.Backend.LEGACY_MEDIA:
            from django.core.files.storage import default_storage
            from django.http import FileResponse
            if not default_storage.exists(ver.storage_key):
                raise StorageError("legacy file is missing")
            response = FileResponse(default_storage.open(ver.storage_key, "rb"), as_attachment=not inline,
                                    filename=ver.original_filename)
            response["X-Content-Type-Options"] = "nosniff"
        else:
            store = get_store()
            size = store.stat(ver.storage_key)
            if size is None or size != ver.size_bytes:
                services.verify_version(ver, user=request.user, request=request)
                raise DocumentError(
                    "FILE IS MISSING OR DAMAGED IN STORAGE - the download was blocked and administrators have been "
                    "alerted in the audit trail. Do not rely on this file.")
            response = store.download(ver.storage_key, ver.original_filename, ver.mime_type or "application/octet-stream",
                                      inline=inline)
    except (DocumentError, StorageError) as exc:
        messages.error(request, str(exc) if isinstance(exc, DocumentError)
                       else "FILE COULD NOT BE RETRIEVED FROM STORAGE - please try again; if it persists tell an administrator.")
        log.error("Download failed for version %s: %s", ver.pk, exc)
        return redirect("documents:detail", pk=doc.pk)
    log_event(user=request.user, action=AuditEvent.Action.DOCUMENT_DOWNLOADED, entity_type="document", entity_id=doc.pk,
              entity_label=ver.original_filename, new={"version": ver.version_number}, request=request)
    return response


def _post_action(request, pk, allowed, func, ok_message, **kw):
    doc = _visible_document(request, pk)
    perm.require(allowed(request.user, doc.job) if allowed.__code__.co_argcount == 2 else allowed(request.user))
    if request.method == "POST":
        try:
            func(document_pk=doc.pk, user=request.user, request=request, **kw)
        except DocumentError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, ok_message)
    return redirect("documents:detail", pk=doc.pk)


@login_required
def version_make_current(request, pk):
    ver = get_object_or_404(DocumentVersion.objects.select_related("document__job"), pk=pk)
    perm.require(perm.can_edit_job(request.user, ver.document.job) and ver.document.job.lifecycle == L.ACTIVE)
    if request.method == "POST":
        try:
            services.make_current(version_pk=ver.pk, user=request.user, request=request)
        except DocumentError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Version {ver.version_number} is now the current version.")
    return redirect("documents:detail", pk=ver.document_id)


@login_required
def document_verify(request, pk):
    doc = _visible_document(request, pk)
    perm.require(perm.can_edit_content(request.user, doc.job))
    if request.method == "POST":
        bad = 0
        try:
            for ver in doc.versions.all():
                if services.verify_version(ver, user=request.user, request=request) not in ("ok",):
                    bad += 1
        except DocumentError as exc:
            messages.error(request, str(exc))
        else:
            if bad:
                messages.error(request, f"INTEGRITY CHECK FAILED - {bad} version(s) are missing or damaged. See the status column.")
            else:
                messages.success(request, "All versions were re-read from storage and match their recorded checksums.")
    return redirect("documents:detail", pk=doc.pk)


@login_required
def document_delete(request, pk):
    return _post_action(request, pk, lambda u, j: perm.can_delete(u) and j.lifecycle == L.ACTIVE,
                        services.soft_delete_document, "Document deleted. All versions are kept and an administrator can restore it.")


@login_required
def document_restore(request, pk):
    return _post_action(request, pk, lambda u, j: perm.can_delete(u), services.restore_document, "Document restored.")


@login_required
def document_purge(request, pk):
    doc = _visible_document(request, pk)
    perm.require(perm.can_delete(request.user))
    if doc.lifecycle != Document.Lifecycle.DELETED:
        messages.error(request, "DELETE THE DOCUMENT FIRST - only deleted documents can be permanently destroyed.")
        return redirect("documents:detail", pk=pk)
    if request.method == "POST":
        try:
            n = services.purge_document(document_pk=pk, user=request.user, typed=request.POST.get("confirm_text", ""),
                                        request=request)
        except DocumentError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Document permanently deleted ({n} version(s)).")
            return redirect("jobs:detail", pk=doc.job_id)
    return render(request, "documents/document_purge_confirm.html", {"doc": doc, "phrase": f"DELETE {doc.filename}"})
