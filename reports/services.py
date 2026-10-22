import csv
import io
import zipfile
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape

from django.http import HttpResponse

from jobs.models import Job

EXPORT_HEADERS = [
    "PILLAR NO",
    "NORTHINGS (M)",
    "EASTINGS (M)",
    "PLAN NO: ORIGIN: REF. PLAN NO.",
    "TITLE",
    "LOCATION",
    "AREA",
]


def _format_decimal(value, places=3):
    if value in (None, ""):
        return ""
    normalised = Decimal(str(value)).quantize(
        Decimal("1").scaleb(-places),
        rounding=ROUND_HALF_UP,
    )
    return format(normalised, f".{places}f")


def _area_text(job):
    area = job.survey_area
    if area is None:
        return ""
    text = _format_decimal(area, 3)
    unit = "SQ.MTRS" if job.area_unit == Job.AreaUnit.SQM else "HECTARES"
    return f"{text} {unit}" if text else unit


def _job_rows(job):
    points = list(job.survey_points.order_by("beacon_number"))
    return [[
        "\n".join(point.beacon_number for point in points),
        "\n".join(_format_decimal(point.northing, 3) for point in points),
        "\n".join(_format_decimal(point.easting, 3) for point in points),
        job.plan_number,
        job.title,
        job.job_address,
        _area_text(job),
    ]]


def _export_jobs_queryset(queryset=None):
    jobs = queryset
    if jobs is None:
        jobs = Job.objects.select_related("client", "assigned_to").prefetch_related("survey_points")
    return jobs.order_by("plan_number")


def jobs_to_csv(queryset=None):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="jobs_report.csv"'

    writer = csv.writer(response)
    writer.writerow(EXPORT_HEADERS)

    for job in _export_jobs_queryset(queryset):
        writer.writerow(_job_rows(job)[0])
    return response


TABLE_COLUMN_WIDTHS = [1800, 1450, 1450, 2400, 2400, 4800, 1800]


def _table_row_xml(cells, header=False):
    xml = "<w:tr>"
    for index, (cell, width) in enumerate(zip(cells, TABLE_COLUMN_WIDTHS)):
        paragraphs = []
        for line in str(cell).split("\n"):
            safe = escape(line, {'"': "&quot;"})
            bold = "<w:b/>" if header else ""
            alignment = '<w:jc w:val="center"/>' if header or index != 5 else ""
            paragraphs.append(
                "<w:p><w:pPr>{}</w:pPr><w:r><w:rPr>{}</w:rPr>"
                '<w:t xml:space="preserve">{}</w:t></w:r></w:p>'.format(
                    alignment, bold, safe
                )
            )
        xml += (
            "<w:tc><w:tcPr><w:tcW w:w=\"{}\" w:type=\"dxa\"/>"
            "<w:vAlign w:val=\"center\"/></w:tcPr>{}</w:tc>"
        ).format(width, "".join(paragraphs))
    xml += "</w:tr>"
    return xml


def jobs_to_docx(queryset=None):
    table_rows = [_table_row_xml(EXPORT_HEADERS, header=True)]
    for job in _export_jobs_queryset(queryset):
        table_rows.append(_table_row_xml(_job_rows(job)[0]))

    table_rows_xml = "".join(table_rows)
    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tblPr>
        <w:tblW w:w="16100" w:type="dxa"/>
        <w:tblLayout w:type="fixed"/>
        <w:tblBorders>
          <w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>
          <w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>
          <w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>
          <w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>
          <w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>
          <w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/>
        </w:tblBorders>
      </w:tblPr>
      <w:tblGrid>
        <w:gridCol w:w="1800"/>
        <w:gridCol w:w="1450"/>
        <w:gridCol w:w="1450"/>
        <w:gridCol w:w="2400"/>
        <w:gridCol w:w="2400"/>
        <w:gridCol w:w="4800"/>
        <w:gridCol w:w="1800"/>
      </w:tblGrid>
      {table_rows_xml}
    </w:tbl>
    <w:p/>
    <w:sectPr>
      <w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>
      <w:pgMar w:top="360" w:right="360" w:bottom="360" w:left="360" w:header="180" w:footer="180" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>'''

    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''

    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''

    word_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''

    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
</w:styles>'''

    core_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>Jobs export</dc:title>
  <dc:creator>SurveyCo</dc:creator>
  <cp:lastModifiedBy>SurveyCo</cp:lastModifiedBy>
</cp:coreProperties>'''

    app_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>SurveyCo</Application>
</Properties>'''

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("docProps/core.xml", core_xml)
        archive.writestr("docProps/app.xml", app_xml)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/styles.xml", styles_xml)
        archive.writestr("word/_rels/document.xml.rels", word_rels)

    response = HttpResponse(zip_buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    response["Content-Disposition"] = 'attachment; filename="jobs_report.docx"'
    return response
