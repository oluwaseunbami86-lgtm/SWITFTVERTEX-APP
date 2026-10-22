from django import forms

from .models import Location


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ["name", "state", "city", "address", "latitude", "longitude"]
