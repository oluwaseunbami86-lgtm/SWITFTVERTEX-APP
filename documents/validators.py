import os
from dataclasses import dataclass

from django.conf import settings

from .storage import hash_stream


class UploadRejected(Exception):
    """The file itself is not acceptable. The message is shown to the user as-is."""


def _is_text(head):
    return b"\x00" not in head


SIGNATURES = {
    # ext: (mime, check(head) -> bool)
    "pdf": ("application/pdf", lambda h: h.startswith(b"%PDF")),
    "dwg": ("application/acad", lambda h: h.startswith(b"AC1")),
    "dxf": ("image/vnd.dxf", lambda h: h.startswith(b"AutoCAD Binary DXF") or (_is_text(h) and b"SECTION" in h)),
    "csv": ("text/csv", _is_text),
    "txt": ("text/plain", _is_text),
    "gpx": ("application/gpx+xml", lambda h: _is_text(h) and b"<" in h),
    "kml": ("application/vnd.google-earth.kml+xml", lambda h: _is_text(h) and b"<" in h),
    "xls": ("application/vnd.ms-excel", lambda h: h.startswith(b"\xd0\xcf\x11\xe0")),
    "doc": ("application/msword", lambda h: h.startswith(b"\xd0\xcf\x11\xe0")),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", lambda h: h.startswith(b"PK\x03\x04")),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", lambda h: h.startswith(b"PK\x03\x04")),
    "zip": ("application/zip", lambda h: h.startswith(b"PK\x03\x04")),
    "jpg": ("image/jpeg", lambda h: h.startswith(b"\xff\xd8\xff")),
    "jpeg": ("image/jpeg", lambda h: h.startswith(b"\xff\xd8\xff")),
    "png": ("image/png", lambda h: h.startswith(b"\x89PNG\r\n\x1a\n")),
    "tif": ("image/tiff", lambda h: h[:4] in (b"II*\x00", b"MM\x00*")),
    "tiff": ("image/tiff", lambda h: h[:4] in (b"II*\x00", b"MM\x00*")),
}
INLINE_SAFE = {"application/pdf", "image/jpeg", "image/png"}


@dataclass
class UploadInfo:
    filename: str
    extension: str
    mime: str
    size: int
    sha256: str


def clean_filename(name):
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    return name[:200]


def inspect_upload(uploaded):
    filename = clean_filename(uploaded.name)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SIGNATURES:
        shown = f".{ext}" if ext else "files without an extension"
        raise UploadRejected(f"FILE TYPE NOT SUPPORTED - {shown} cannot be stored. Allowed: " + ", ".join(sorted(SIGNATURES)) + ".")
    mime, check = SIGNATURES[ext]
    sha256, size = hash_stream(uploaded)
    if size == 0:
        raise UploadRejected("FILE IS EMPTY - nothing was stored.")
    limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    if size > limit:
        raise UploadRejected(f"FILE TOO LARGE - the limit is {settings.MAX_UPLOAD_MB} MB.")
    head = uploaded.read(8192)
    uploaded.seek(0)
    if not check(head):
        raise UploadRejected(f"FILE CONTENT DOES NOT MATCH ITS .{ext.upper()} EXTENSION - it was not stored.")
    return UploadInfo(filename=filename, extension=ext, mime=mime, size=size, sha256=sha256)
