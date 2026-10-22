from django import forms
from django.forms import formset_factory

from .models import SurveyPoint


class SurveyPointForm(forms.ModelForm):
    class Meta:
        model = SurveyPoint
        fields = ["job", "beacon_number", "northing", "easting", "height", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class ManualSurveyPointForm(forms.ModelForm):
    class Meta:
        model = SurveyPoint
        fields = ["beacon_number", "easting", "northing", "height"]
        widgets = {
            "beacon_number": forms.TextInput(attrs={"autocomplete": "off"}),
            "easting": forms.NumberInput(attrs={"step": "any"}),
            "northing": forms.NumberInput(attrs={"step": "any"}),
            "height": forms.NumberInput(attrs={"step": "any"}),
        }


ManualSurveyPointFormSet = formset_factory(
    ManualSurveyPointForm,
    extra=1,
    can_delete=True,
    max_num=500,
    validate_max=True,
)
