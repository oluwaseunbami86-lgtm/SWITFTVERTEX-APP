(function () {
    "use strict";

    var tabs = document.querySelectorAll("[data-entry-tab]");
    var panels = document.querySelectorAll("[data-entry-panel]");
    tabs.forEach(function (tab) {
        tab.addEventListener("click", function () {
            var selected = tab.dataset.entryTab;
            tabs.forEach(function (item) {
                var active = item === tab;
                item.classList.toggle("is-active", active);
                item.setAttribute("aria-selected", active ? "true" : "false");
            });
            panels.forEach(function (panel) {
                panel.hidden = panel.dataset.entryPanel !== selected;
            });
        });
    });

    var manualForm = document.querySelector("[data-manual-form]");
    if (manualForm) {
        var manualRows = manualForm.querySelector("[data-manual-rows]");
        var rowTemplate = manualForm.querySelector("[data-manual-row-template]");
        var totalForms = manualForm.querySelector("#id_points-TOTAL_FORMS");
        var maxManual = Number(manualForm.dataset.maxManualRecords || 500);

        manualForm.querySelector("[data-add-row]").addEventListener("click", function () {
            var index = Number(totalForms.value);
            if (index >= maxManual) {
                window.alert("A maximum of " + maxManual + " manual records can be saved at once.");
                return;
            }
            var rowHtml = rowTemplate.innerHTML.replace(/__prefix__/g, String(index));
            manualRows.insertAdjacentHTML("beforeend", rowHtml);
            totalForms.value = String(index + 1);
        });

        manualRows.addEventListener("click", function (event) {
            var button = event.target.closest("[data-remove-row]");
            if (!button) return;
            var row = button.closest("tr");
            var deleteInput = row.querySelector('input[type="checkbox"][name$="-DELETE"]');
            if (deleteInput) {
                deleteInput.checked = true;
                row.hidden = true;
            } else {
                row.remove();
            }
        });
    }

    var uploadForm = document.querySelector("[data-upload-form]");
    if (uploadForm) {
        var fileInput = uploadForm.querySelector("[data-file-input]");
        var dropArea = uploadForm.querySelector("[data-drop-area]");
        var filename = uploadForm.querySelector("[data-file-name]");
        var uploadButton = uploadForm.querySelector("[data-upload-button]");
        var maxBytes = Number(uploadForm.dataset.maxBytes || (4 * 1024 * 1024));

        function readFileSelection(file) {
            if (!file) return;
            filename.textContent = file.name;
            if (!/\.(csv|txt)$/i.test(file.name)) {
                window.alert("Select a .csv or .txt file.");
                fileInput.value = "";
                filename.textContent = "No file selected";
                return;
            }
            if (file.size > maxBytes) {
                window.alert("The selected file exceeds the 4 MB limit.");
                fileInput.value = "";
                filename.textContent = "No file selected";
                return;
            }
            uploadButton.disabled = false;
            uploadButton.textContent = "Reading " + file.name + "…";
            uploadForm.requestSubmit();
        }

        fileInput.addEventListener("change", function () {
            readFileSelection(fileInput.files[0]);
        });
        ["dragenter", "dragover"].forEach(function (eventName) {
            dropArea.addEventListener(eventName, function (event) {
                event.preventDefault();
                dropArea.classList.add("is-dragging");
            });
        });
        ["dragleave", "drop"].forEach(function (eventName) {
            dropArea.addEventListener(eventName, function (event) {
                event.preventDefault();
                dropArea.classList.remove("is-dragging");
            });
        });
        dropArea.addEventListener("drop", function (event) {
            var files = event.dataTransfer.files;
            if (!files.length) return;
            try {
                fileInput.files = files;
                readFileSelection(files[0]);
            } catch (error) {
                window.alert("Use the file selection button to choose the file.");
            }
        });
    }

    var mappingForm = document.querySelector("[data-mapping-form]");
    if (mappingForm) {
        var stage = JSON.parse(document.getElementById("coordinate-stage-data").textContent);
        var headers = JSON.parse(document.getElementById("coordinate-header-data").textContent);
        var suggestions = JSON.parse(document.getElementById("coordinate-suggestion-data").textContent);
        var layoutSelect = mappingForm.querySelector("[data-layout]");
        var columnArea = mappingForm.querySelector("[data-column-configuration]");
        var verticalArea = mappingForm.querySelector("[data-vertical-configuration]");
        var headerCheckbox = mappingForm.querySelector("[data-has-header]");
        var columnBody = mappingForm.querySelector("[data-column-mappings]");
        var lineBody = mappingForm.querySelector("[data-line-mappings]");
        var linesPerRecord = mappingForm.querySelector("[data-lines-per-record]");
        var mappingError = mappingForm.querySelector("[data-mapping-error]");
        var fields = [
            ["", "Do not import"],
            ["beacon_number", "Pillar/Point Number"],
            ["plan_number", "Plan Number"],
            ["easting", "Easting"],
            ["northing", "Northing"],
            ["height", "Height"]
        ];
        var columnCount = stage.column_count;

        function optionMarkup(selected) {
            return fields.map(function (field) {
                var option = document.createElement("option");
                option.value = field[0];
                option.textContent = field[1];
                if (field[0] === selected) option.selected = true;
                return option.outerHTML;
            }).join("");
        }

        function renderColumnMappings() {
            var header = headerCheckbox.checked ? (stage.rows[0] || []) : [];
            columnBody.replaceChildren();
            for (var index = 0; index < columnCount; index += 1) {
                var tr = document.createElement("tr");
                var title = header[index] || ("Column " + (index + 1));
                var sampleRow = stage.rows[headerCheckbox.checked ? 1 : 0] || stage.rows[0] || [];
                var sample = sampleRow[index] || "";
                var select = document.createElement("select");
                select.dataset.columnIndex = String(index);
                select.setAttribute("aria-label", "Assign " + title);
                var suggestedField = "";
                Object.keys(suggestions).forEach(function (field) {
                    if (Number(suggestions[field]) === index) suggestedField = field;
                });
                select.innerHTML = optionMarkup(suggestedField);
                var label = document.createElement("td");
                label.textContent = title;
                var example = document.createElement("td");
                example.textContent = sample;
                var assignment = document.createElement("td");
                assignment.appendChild(select);
                tr.append(label, example, assignment);
                columnBody.appendChild(tr);
            }
        }

        function renderLineMappings() {
            var count = Math.max(1, Math.min(20, Number(linesPerRecord.value) || 4));
            linesPerRecord.value = String(count);
            var first = stage.vertical_lines[0] || "";
            var sampleLines = first.split(/[,;\t|]/);
            if (sampleLines.length === 1) sampleLines = [first];
            lineBody.replaceChildren();
            for (var index = 0; index < count; index += 1) {
                var tr = document.createElement("tr");
                var line = document.createElement("td");
                line.textContent = "Line " + (index + 1);
                var example = document.createElement("td");
                example.textContent = sampleLines[index] || "";
                var select = document.createElement("select");
                select.dataset.lineIndex = String(index);
                select.setAttribute("aria-label", "Assign line " + (index + 1));
                select.innerHTML = optionMarkup("");
                var assignment = document.createElement("td");
                assignment.appendChild(select);
                tr.append(line, example, assignment);
                lineBody.appendChild(tr);
            }
        }

        function updateLayout() {
            var vertical = layoutSelect.value === "vertical";
            columnArea.hidden = vertical;
            verticalArea.hidden = !vertical;
            renderColumnMappings();
            renderLineMappings();
        }

        layoutSelect.addEventListener("change", updateLayout);
        headerCheckbox.addEventListener("change", renderColumnMappings);
        linesPerRecord.addEventListener("change", renderLineMappings);
        updateLayout();

        mappingForm.addEventListener("submit", function (event) {
            var mapping = {};
            var assignments = [];
            if (layoutSelect.value === "vertical") {
                lineBody.querySelectorAll("select").forEach(function (select) {
                    if (select.value) assignments.push([select.value, Number(select.dataset.lineIndex)]);
                });
            } else {
                columnBody.querySelectorAll("select").forEach(function (select) {
                    if (select.value) assignments.push([select.value, Number(select.dataset.columnIndex)]);
                });
            }
            var assignedFields = {};
            var duplicateField = assignments.some(function (assignment) {
                if (assignedFields[assignment[0]]) return true;
                assignedFields[assignment[0]] = true;
                return false;
            });
            var missingFields = ["beacon_number", "easting", "northing", "height"].filter(function (field) {
                return !assignedFields[field];
            });
            if (duplicateField || missingFields.length) {
                event.preventDefault();
                mappingError.hidden = false;
                mappingError.textContent = duplicateField
                    ? "Assign each survey field to only one source column or line."
                    : "Map each required field before preview: " + missingFields.join(", ") + ".";
                return;
            }
            assignments.forEach(function (assignment) {
                mapping[assignment[0]] = assignment[1];
            });
            mappingError.hidden = true;
            mappingForm.querySelector("[data-mapping-json]").value = JSON.stringify({
                layout: layoutSelect.value,
                has_header: headerCheckbox.checked,
                lines_per_record: Number(linesPerRecord.value),
                mapping: mapping
            });
        });
    }

    var confirmForm = document.querySelector("[data-confirm-form]");
    if (confirmForm) {
        var previewRows = Array.from(confirmForm.querySelectorAll("[data-preview-row]"));
        var errorData = JSON.parse(document.getElementById("coordinate-error-data").textContent);
        var issuesByRow = {};
        errorData.forEach(function (issue) {
            if (issue.row > 0) {
                issuesByRow[issue.row] = issuesByRow[issue.row] || [];
                issuesByRow[issue.row].push(issue);
            }
        });
        previewRows.forEach(function (row, index) {
            row.querySelectorAll("[data-field]").forEach(function (input) {
                input.dataset.initial = input.value;
            });
        });

        var validCount = 0;
        var errorCount = 0;
        var duplicateCount = 0;
        var globalErrors = errorData.some(function (issue) {
            return issue.row <= 0 || issue.row > previewRows.length;
        });

        function validatePreview() {
            validCount = 0;
            errorCount = 0;
            duplicateCount = 0;
            var pointKeys = {};
            var duplicateRows = {};
            previewRows.forEach(function (row, index) {
                var pointNumber = row.querySelector('[data-field="beacon_number"]').value.trim().toLocaleLowerCase();
                if (!pointNumber) return;
                if (pointKeys[pointNumber] !== undefined) {
                    duplicateRows[index + 1] = true;
                    duplicateRows[pointKeys[pointNumber]] = true;
                } else {
                    pointKeys[pointNumber] = index + 1;
                }
            });
            duplicateCount = Object.keys(duplicateRows).length;
            previewRows.forEach(function (row, index) {
                var errors = [];
                var values = {};
                row.querySelectorAll("[data-field]").forEach(function (input) {
                    values[input.dataset.field] = input.value.trim();
                    (issuesByRow[index + 1] || []).forEach(function (issue) {
                        if (issue.field === input.dataset.field && input.value === input.dataset.initial) {
                            errors.push(issue.message);
                        }
                    });
                });
                if (!values.beacon_number) errors.push("Point number is required.");
                ["easting", "northing", "height"].forEach(function (field) {
                    if (!values[field]) errors.push(field + " is required.");
                    else {
                        var numberParts = values[field].match(/^[+-]?(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:e([+-]?\d+))?$/i);
                        if (!numberParts || !Number.isFinite(Number(values[field]))) {
                            errors.push(field + " must be numeric.");
                        } else {
                            var fraction = numberParts[2] || numberParts[3] || "";
                            var exponent = Number(numberParts[4] || 0);
                            if (fraction.length - exponent > 4) errors.push(field + " allows at most 4 decimal places.");
                        }
                    }
                });
                if (!values.plan_number) errors.push("Plan number is required.");
                else if (values.plan_number.toLocaleLowerCase() !== confirmForm.dataset.planNumber.toLocaleLowerCase()) {
                    errors.push("Plan number must match the selected job.");
                }
                if (duplicateRows[index + 1]) errors.push("Duplicate point number in this file.");
                var status = row.querySelector("[data-row-status]");
                status.textContent = errors.length ? errors.join(" ") : "Ready";
                row.classList.toggle("has-row-error", errors.length > 0);
                if (errors.length) errorCount += 1;
                else validCount += 1;
            });
            var ready = errorCount === 0 && !globalErrors ? validCount : 0;
            document.querySelector("[data-valid-count]").textContent = String(validCount);
            document.querySelector("[data-error-count]").textContent = String(errorCount + (globalErrors ? 1 : 0));
            document.querySelector("[data-duplicate-count]").textContent = String(duplicateCount);
            document.querySelector("[data-ready-count]").textContent = String(ready);
            confirmForm.querySelector("[data-import-button]").disabled = errorCount > 0 || globalErrors;
            var serverErrors = document.querySelector("[data-server-errors]");
            if (serverErrors) serverErrors.hidden = errorCount === 0 && !globalErrors;
        }

        confirmForm.addEventListener("input", validatePreview);
        confirmForm.addEventListener("submit", function (event) {
            validatePreview();
            if (errorCount) {
                event.preventDefault();
                return;
            }
            var records = previewRows.map(function (row) {
                var record = {};
                row.querySelectorAll("[data-field]").forEach(function (input) {
                    record[input.dataset.field] = input.value.trim();
                });
                return record;
            });
            confirmForm.querySelector("[data-records-json]").value = JSON.stringify(records);
            var button = confirmForm.querySelector("[data-import-button]");
            button.disabled = true;
            button.textContent = "Importing records…";
            var progress = confirmForm.querySelector("[data-import-progress]");
            progress.hidden = false;
            progress.removeAttribute("value");
        });
        validatePreview();
    }
})();
