from django import forms

from .models import Client


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "client_type", "email", "phone_number", "address", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
