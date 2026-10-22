from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory

from survey_points.models import SurveyPoint

from .models import Job, Project, plan_number_validator


class PlanNumberField(forms.CharField):
    """Trims and upper-cases before validating, so 'od/1379/2026/041 ' is accepted as OD/1379/2026/041."""

    def to_python(self, value):
        value = super().to_python(value)
        return value.strip().upper() if value else value


class JobForm(forms.ModelForm):
    plan_number = PlanNumberField(
        max_length=100, validators=[plan_number_validator],
        widget=forms.TextInput(attrs={"placeholder": "OD/1379/2026/041", "autocomplete": "off"}),
    )
    # Optimistic locking token: the version the user was looking at.
    expected_version = forms.IntegerField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Job
        fields = [
            "survey_type", "job_type", "job_date", "title", "client", "project", "job_address",
            "survey_area", "area_unit", "plan_number",
            "has_reference_number", "reference_number",
            "status", "assigned_to", "description",
        ]
        labels = {
            "survey_type": "Survey Type",
            "job_type": "Job Type",
            "job_date": "Job date",
            "has_reference_number": "Has referenced plan?",
        }
        widgets = {
            "job_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "job_address": forms.TextInput(attrs={"placeholder": "e.g. Plot 14, Lekki Phase 2, Lagos"}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.duplicate_job = None
        # Preserve backward compatibility with older job creation payloads while
        # still exposing the new Survey Type field to the UI.
        self.fields["survey_type"].required = False
        self.fields["job_type"].required = False
        # Required per spec: job type, job date, job title, job address, survey area, plan number.
        # Client and project are optional.
        self.fields["client"].required = False
        self.fields["project"].required = False
        self.fields["project"].queryset = Project.objects.select_related("client")
        if user:
            from accounts.permissions import role_of
            from accounts.models import Profile

            if role_of(user) == Profile.Role.SURVEYOR:
                client_filter = Q(created_by=user)
                project_filter = Q(client__created_by=user)
                assignee_filter = Q(pk=user.pk)
                if self.instance.pk and self.instance.client_id:
                    client_filter |= Q(pk=self.instance.client_id)
                if self.instance.pk and self.instance.project_id:
                    project_filter |= Q(pk=self.instance.project_id)
                if self.instance.pk and self.instance.assigned_to_id:
                    assignee_filter |= Q(pk=self.instance.assigned_to_id)
                self.fields["client"].queryset = self.fields["client"].queryset.filter(client_filter)
                self.fields["project"].queryset = self.fields["project"].queryset.filter(project_filter)
                self.fields["assigned_to"].queryset = self.fields["assigned_to"].queryset.filter(assignee_filter)
        self.fields["reference_number"].required = False
        if self.instance.pk:
            self.fields["expected_version"].initial = self.instance.row_version
            self.initial["expected_version"] = self.instance.row_version

    def clean_plan_number(self):
        value = self.cleaned_data["plan_number"]
        existing = Job.all_objects.filter(plan_number__iexact=value).exclude(pk=self.instance.pk).first()
        if existing:
            self.duplicate_job = existing
            raise forms.ValidationError("JOB NUMBER ALREADY EXISTS", code="duplicate_plan_number")
        return value

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("survey_type") and cleaned_data.get("job_type"):
            surv = cleaned_data["job_type"]
            cleaned_data["survey_type"] = surv if surv in dict(Job.SurveyType.choices) else Job.SurveyType.BOUNDARY
        if cleaned_data.get("has_reference_number") and not cleaned_data.get("reference_number"):
            self.add_error("reference_number", "Enter the reference number, or uncheck 'Has reference number'.")
        client, project = cleaned_data.get("client"), cleaned_data.get("project")
        if project:
            if client and project.client_id != client.pk:
                self.add_error("project", "THIS PROJECT BELONGS TO A DIFFERENT CLIENT")
            elif not client:
                cleaned_data["client"] = project.client
        return cleaned_data


class BeaconForm(forms.ModelForm):
    class Meta:
        model = SurveyPoint
        fields = ["beacon_number", "northing", "easting", "height"]
        widgets = {
            "beacon_number": forms.TextInput(attrs={"class": "beacon-number-input"}),
            "northing": forms.NumberInput(attrs={"step": "any"}),
            "easting": forms.NumberInput(attrs={"step": "any"}),
            "height": forms.NumberInput(attrs={"step": "any"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = True


# One Job -> many SurveyPoint (beacons). extra=0: the page's JS generates
# rows client-side based on the "Number of beacons" input, rather than a
# fixed number of blank rows always being shown.
BeaconFormSet = inlineformset_factory(
    Job,
    SurveyPoint,
    form=BeaconForm,
    fields=["beacon_number", "northing", "easting", "height"],
    extra=0,
    can_delete=True,
)
