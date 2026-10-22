import csv
import io
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.permissions import can_edit_content
from audit.models import AuditEvent
from audit.services import log_event

from .models import SurveyPoint

MAX_IMPORT_BYTES = 4 * 1024 * 1024
MAX_IMPORT_RECORDS = 5000
MAX_MANUAL_RECORDS = 500

IMPORT_FIELDS = ("beacon_number", "plan_number", "easting", "northing", "height")
COORDINATE_FIELDS = ("easting", "northing", "height")


def parse_coordinate_file(uploaded_file):
    if uploaded_file.size > MAX_IMPORT_BYTES:
        raise ValueError("Files must be 4 MB or smaller.")

    raw = uploaded_file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise ValueError("The file encoding is not supported. Save it as UTF-8 CSV or text.") from exc
    if "\x00" in text:
        raise ValueError("The file contains binary data and cannot be imported as text.")
    line_breaks = text.count("\n") or text.count("\r")
    if line_breaks > MAX_IMPORT_RECORDS * 20 + 1:
        raise ValueError(f"The file has too many lines. Limit files to {MAX_IMPORT_RECORDS} records.")

    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("The selected file is empty.")

    sample = "\n".join(lines[:20])
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        delimiter = None

    if delimiter:
        if any(line.count(delimiter) >= 100 for line in lines):
            raise ValueError("Each source row is limited to 100 columns.")
        rows = []
        for row in csv.reader(io.StringIO(text), delimiter=delimiter):
            if len(row) > 100:
                raise ValueError("Each source row is limited to 100 columns.")
            rows.append([cell.strip() for cell in row] or [""])
    else:
        split_lines = [line.split() for line in lines]
        widths = {len(row) for row in split_lines}
        rows = split_lines if len(widths) == 1 and next(iter(widths)) > 1 else [[line.strip()] for line in lines]
        delimiter = "whitespace" if len(widths) == 1 and next(iter(widths)) > 1 else ""
        if any(len(row) > 100 for row in rows):
            raise ValueError("Each source row is limited to 100 columns.")

    if len(rows) > MAX_IMPORT_RECORDS * 20 + 1:
        raise ValueError(f"The file has too many rows. Limit files to {MAX_IMPORT_RECORDS} records.")
    if not rows:
        raise ValueError("No coordinate rows were detected in the file.")

    vertical_lines = [line.strip() for line in lines]
    return {
        "rows": rows,
        "vertical_lines": vertical_lines,
        "delimiter": delimiter,
        "mode_guess": "vertical" if all(len(row) == 1 for row in rows) else "columns",
    }


def suggest_header_mapping(header):
    patterns = {
        "beacon_number": ("point", "pillar", "beacon", "station", "number", "id"),
        "plan_number": ("plan", "job number", "plan number"),
        "easting": ("easting", "east", "e"),
        "northing": ("northing", "north", "n"),
        "height": ("height", "elevation", "elev", "rl", "z"),
    }
    mapping = {}
    for index, name in enumerate(header):
        normalized = name.strip().lower().replace("_", " ").replace("-", " ")
        for field, aliases in patterns.items():
            if any(normalized == alias or normalized.startswith(alias + " ") for alias in aliases):
                if field not in mapping:
                    mapping[field] = index
                break
    return mapping


def build_import_records(stage, configuration, plan_number):
    records = []
    issues = []
    layout = configuration.get("layout")
    mapping = configuration.get("mapping", {})
    if layout not in {"columns", "vertical"} or not isinstance(mapping, dict):
        return [], [{"row": 0, "field": "", "message": "The selected file structure or field mapping is invalid."}]

    if any(field not in IMPORT_FIELDS for field in mapping):
        return [], [{"row": 0, "field": "", "message": "The mapping contains an unsupported survey field."}]
    required_fields = {"beacon_number", "easting", "northing", "height"}
    if not required_fields.issubset(mapping):
        issues.append({"row": 0, "field": "", "message": "Map point number, easting, northing, and height before previewing."})

    source_indexes = []
    normalized_mapping = {}
    for field, raw_index in mapping.items():
        if raw_index in ("", None):
            continue
        if isinstance(raw_index, bool) or not isinstance(raw_index, int) or raw_index < 0:
            issues.append({"row": 0, "field": field, "message": "A source mapping index is invalid."})
            continue
        normalized_mapping[field] = raw_index
        source_indexes.append(raw_index)
    mapping = normalized_mapping
    if len(source_indexes) != len(set(source_indexes)):
        issues.append({"row": 0, "field": "", "message": "Each survey field must map to a different source column or line."})

    if layout == "vertical":
        lines_per_record = configuration.get("lines_per_record")
        try:
            lines_per_record = int(lines_per_record)
        except (TypeError, ValueError):
            return [], [{"row": 0, "field": "", "message": "Enter a valid number of lines per record."}]
        if lines_per_record < 1 or lines_per_record > 20:
            return [], [{"row": 0, "field": "", "message": "Records must contain between 1 and 20 lines."}]
        if any(index >= lines_per_record for index in mapping.values()):
            issues.append({"row": 0, "field": "", "message": "A mapped line is outside the configured record structure."})
        lines = stage["vertical_lines"]
        for offset in range(0, len(lines), lines_per_record):
            source = lines[offset:offset + lines_per_record]
            row_number = len(records) + 1
            if len(source) != lines_per_record:
                issues.append({"row": row_number, "field": "", "message": "Incomplete record: fewer lines than configured."})
                continue
            record = {field: "" for field in IMPORT_FIELDS}
            for field, index in mapping.items():
                if field not in IMPORT_FIELDS:
                    continue
                try:
                    record[field] = source[int(index)]
                except (TypeError, ValueError, IndexError):
                    issues.append({"row": row_number, "field": field, "message": "The selected line mapping is invalid."})
            records.append(record)
    else:
        rows = stage["rows"]
        has_header_value = configuration.get("has_header", False)
        if not isinstance(has_header_value, bool):
            return [], [{"row": 0, "field": "", "message": "The header-row setting is invalid."}]
        has_header = has_header_value
        if has_header:
            rows = rows[1:]
        if any(index >= max((len(row) for row in stage["rows"]), default=0) for index in mapping.values()):
            issues.append({"row": 0, "field": "", "message": "A mapped column is not present in the selected file."})
        for row_number, source in enumerate(rows, 1):
            record = {field: "" for field in IMPORT_FIELDS}
            for field, index in mapping.items():
                if field not in IMPORT_FIELDS or index in ("", None):
                    continue
                try:
                    record[field] = source[int(index)]
                except (TypeError, ValueError, IndexError):
                    issues.append({"row": row_number, "field": field, "message": "The selected column is missing from this row."})
            records.append(record)

    if len(records) > MAX_IMPORT_RECORDS:
        issues.append({"row": 0, "field": "", "message": f"Imports are limited to {MAX_IMPORT_RECORDS} records."})
        return [], issues

    for record in records:
        if not record.get("plan_number"):
            record["plan_number"] = plan_number
    return records, issues


def validate_survey_point_records(job, rows, captured_by=None):
    issues = []
    validated = []
    seen = set()
    existing = {
        value.casefold()
        for value in SurveyPoint.objects.filter(job=job).values_list("beacon_number", flat=True)
    }

    if not isinstance(rows, list) or not rows:
        return [], [{"row": 0, "field": "", "message": "Provide at least one coordinate record."}]
    if len(rows) > MAX_IMPORT_RECORDS:
        return [], [{"row": 0, "field": "", "message": f"Imports are limited to {MAX_IMPORT_RECORDS} records."}]

    for row_number, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            issues.append({"row": row_number, "field": "", "message": "Record must contain mapped field values."})
            continue
        point_number = str(row.get("beacon_number", "")).strip()
        plan = str(row.get("plan_number", "") or job.plan_number).strip()
        errors = []
        if not point_number:
            errors.append({"row": row_number, "field": "beacon_number", "message": "Pillar/point number is required."})
        elif len(point_number) > 100:
            errors.append({"row": row_number, "field": "beacon_number", "message": "Pillar/point number must be 100 characters or fewer."})

        if not plan:
            errors.append({"row": row_number, "field": "plan_number", "message": "Plan number is required."})
        elif plan.casefold() != job.plan_number.casefold():
            errors.append({
                "row": row_number,
                "field": "plan_number",
                "message": f"Plan number must match the selected job ({job.plan_number}).",
            })

        coordinates = {}
        for field in COORDINATE_FIELDS:
            value = str(row.get(field, "")).strip()
            if not value:
                errors.append({"row": row_number, "field": field, "message": f"{field.title()} is required."})
                continue
            try:
                number = Decimal(value)
                if not number.is_finite():
                    raise InvalidOperation
                coordinates[field] = number
            except (InvalidOperation, ValueError):
                errors.append({"row": row_number, "field": field, "message": f"{field.title()} must be a valid number."})

        normalized = point_number.casefold()
        if point_number and normalized in seen:
            errors.append({"row": row_number, "field": "beacon_number", "message": "Duplicate point number in this file."})
        elif point_number:
            seen.add(normalized)
        if point_number and normalized in existing:
            errors.append({"row": row_number, "field": "beacon_number", "message": "This point number already exists for the selected job."})

        if not errors:
            point = SurveyPoint(
                job=job,
                beacon_number=point_number,
                easting=coordinates["easting"],
                northing=coordinates["northing"],
                height=coordinates["height"],
                captured_by=captured_by,
            )
            try:
                point.full_clean()
            except ValidationError as exc:
                for field, messages in exc.message_dict.items():
                    errors.extend({"row": row_number, "field": field, "message": message} for message in messages)
            else:
                validated.append(point)
        issues.extend(errors)

    return validated, issues


@transaction.atomic
def save_survey_point_records(*, job, user, rows, request=None):
    job = type(job).objects.select_for_update().get(pk=job.pk)
    if job.lifecycle != job.Lifecycle.ACTIVE or not can_edit_content(user, job):
        return [], [{"row": 0, "field": "", "message": "You no longer have permission to add points to this job."}]
    points, issues = validate_survey_point_records(job, rows, captured_by=user)
    if issues:
        return [], issues
    SurveyPoint.objects.bulk_create(points, batch_size=500)
    log_event(
        user=user,
        action=AuditEvent.Action.BEACONS_CHANGED,
        entity_type="job",
        entity_id=job.pk,
        entity_label=job.plan_number,
        new={"imported_count": len(points)},
        description=f"Imported {len(points)} survey point(s) for {job.plan_number}",
        request=request,
    )
    return points, []
