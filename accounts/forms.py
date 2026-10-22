from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

from clients.models import Client
from .models import Profile


class SignupForm(forms.Form):
    username = forms.CharField(max_length=150)
    full_name = forms.CharField(max_length=120, label="Full name")
    email = forms.EmailField()
    phone_number = forms.CharField(max_length=20, required=False, label="Phone number")
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off", "class": "honeypot"}),
    )

    def clean_username(self):
        username = self.cleaned_data["username"].strip().lower()
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise ValidationError("A user with that username already exists.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise ValidationError("A user with that email already exists.")
        return email

    def clean(self):
        data = super().clean()
        password1, password2 = data.get("password1"), data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Passwords do not match.")
        if password1:
            full_name = data.get("full_name", "").strip().split(" ", 1)
            user = get_user_model()(
                username=data.get("username", ""),
                email=data.get("email", ""),
                first_name=full_name[0] if full_name else "",
                last_name=full_name[1] if len(full_name) > 1 else "",
            )
            try:
                validate_password(password1, user=user)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return data


class ApprovalForm(forms.Form):
    role = forms.ChoiceField(choices=Profile.Role.choices)
    client = forms.ModelChoiceField(queryset=Client.objects.all(), required=False)
    rejection_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self):
        data = super().clean()
        if data.get("role") == Profile.Role.CLIENT_VIEWER and not data.get("client"):
            self.add_error("client", "Select the client this account may access.")
        return data


class RejectionForm(forms.Form):
    rejection_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class RoleChangeForm(forms.Form):
    role = forms.ChoiceField(choices=Profile.Role.choices)
    client = forms.ModelChoiceField(queryset=Client.objects.all(), required=False)

    def clean(self):
        data = super().clean()
        if data.get("role") == Profile.Role.CLIENT_VIEWER and not data.get("client"):
            self.add_error("client", "Select the linked client for a Client viewer.")
        return data


class AdminSetPasswordForm(SetPasswordForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)


class AccountPasswordChangeForm(PasswordChangeForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.fields["old_password"].label = "Current password"
        self.fields["new_password1"].label = "New password"
        self.fields["new_password2"].label = "Confirm new password"


class ProfileForm(forms.ModelForm):
    full_name = forms.CharField(max_length=300, label="Full name")
    email = forms.EmailField(label="Email address")
    profile_picture = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        validators=[FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "webp"])],
        help_text="JPG, PNG or WebP; maximum 5 MB.",
    )
    remove_profile_picture = forms.BooleanField(required=False, label="Remove profile picture")

    class Meta:
        model = Profile
        fields = ["phone_number", "profile_picture"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if self.user:
            self.fields["full_name"].initial = self.user.get_full_name()
            self.fields["email"].initial = self.user.email

    def clean_full_name(self):
        full_name = " ".join(self.cleaned_data["full_name"].split())
        if not full_name:
            raise ValidationError("Enter your full name.")
        first_name, _, last_name = full_name.partition(" ")
        if len(first_name) > 150 or len(last_name) > 150:
            raise ValidationError("First and last names must each be 150 characters or fewer.")
        return full_name

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if self.user and get_user_model().objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise ValidationError("A user with that email address already exists.")
        return email

    def clean_profile_picture(self):
        picture = self.cleaned_data.get("profile_picture")
        if picture and picture.size > 5 * 1024 * 1024:
            raise ValidationError("Profile pictures must be 5 MB or smaller.")
        return picture

    def save(self, commit=True):
        profile = super().save(commit=False)
        if self.user:
            first_name, _, last_name = self.cleaned_data["full_name"].partition(" ")
            self.user.first_name = first_name
            self.user.last_name = last_name
            self.user.email = self.cleaned_data["email"]
        if self.cleaned_data.get("remove_profile_picture"):
            profile.profile_picture = ""
        if commit:
            if self.user:
                self.user.save()
            profile.save()
        return profile
