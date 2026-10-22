from django import forms

from .models import Document


class UploadForm(forms.Form):
    file = forms.FileField()
    doc_type = forms.ChoiceField(choices=Document.DocType.choices, label="Type")
    title = forms.CharField(max_length=255, required=False, label="Title (optional)")
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Description (optional)")
    change_note = forms.CharField(max_length=500, required=False, label="What changed? (optional)")
    conflict_choice = forms.ChoiceField(
        required=False, choices=[("", ""), ("new_version", "Keep both - save as a new version"),
                                 ("overwrite", "Overwrite the current file")],
        widget=forms.RadioSelect,
    )
