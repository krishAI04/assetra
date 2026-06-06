from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from .models import (
    Asset,
    AssetDistribution,
    Beneficiary,
    Client,
    Comment,
    Document,
    DocumentVersion,
    Firm,
    Task,
    User,
)


class StyledAuthenticationForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={"placeholder": "Username"}))
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Password"})
    )


class SignupForm(forms.Form):
    firm_name = forms.CharField(max_length=255, label="Firm name")
    first_name = forms.CharField(max_length=150)
    
    last_name = forms.CharField(max_length=150)
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"placeholder": "Create a password"}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs={"placeholder": "Confirm password"}),
    )

    def clean_firm_name(self):
        firm_name = self.cleaned_data["firm_name"]
        if Firm.objects.filter(name__iexact=firm_name).exists():
            raise ValidationError("A firm with this name already exists.")
        return firm_name

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("This username is already in use.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("This email is already in use.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2:
            if password1 != password2:
                raise ValidationError("Passwords do not match.")

            preview_user = User(
                first_name=cleaned_data.get("first_name", ""),
                last_name=cleaned_data.get("last_name", ""),
                username=cleaned_data.get("username", ""),
                email=cleaned_data.get("email", ""),
            )

            password_validation.validate_password(password1, user=preview_user)

    def save(self):
        firm = Firm.objects.create(name=self.cleaned_data["firm_name"])
        user = User(
            firm=firm,
            role=User.Role.ADMIN,
            first_name=self.cleaned_data["first_name"],
            last_name=self.cleaned_data["last_name"],
            username=self.cleaned_data["username"],
            email=self.cleaned_data["email"],
            is_active=True,
        )
        user.set_password(self.cleaned_data["password1"])
        user.full_clean()
        user.save()
        return user


class FirmScopedModelForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


class ClientForm(FirmScopedModelForm):
    class Meta:
        model = Client
        fields = [
            "first_name",
            "last_name",
            "email",
            "phone",
            "address",
            "status",
            "date_of_death",
            "notes",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 4}),
            "date_of_death": forms.DateInput(attrs={"type": "date"}),
            "phone":forms.NumberInput
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if user and user.firm_id:
            self.instance.firm = user.firm


class AssetForm(FirmScopedModelForm):
    class Meta:
        model = Asset
        fields = [
            "client",
            "title",
            "category",
            "description",
            "estimated_value",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if user and user.firm_id:
            self.instance.firm = user.firm
            self.fields["client"].queryset = Client.objects.filter(firm=user.firm).order_by(
                "last_name", "first_name"
            )


class BeneficiaryForm(FirmScopedModelForm):
    class Meta:
        model = Beneficiary
        fields = [
            "client",
            "first_name",
            "last_name",
            "email",
            "phone",
            "relationship_to_client",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if user and user.firm_id:
            self.instance.firm = user.firm
            self.fields["client"].queryset = Client.objects.filter(firm=user.firm).order_by(
                "last_name", "first_name"
            )


class DocumentForm(FirmScopedModelForm):
    asset_client_map = {}

    class Meta:
        model = Document
        fields = ["title", "client", "asset", "file"]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if user and user.firm_id:
            self.instance.firm = user.firm
            self.fields["client"].queryset = Client.objects.filter(firm=user.firm).order_by(
                "last_name", "first_name"
            )
            assets = Asset.objects.filter(firm=user.firm).order_by("title")
            self.fields["asset"].queryset = assets
            self.asset_client_map = {str(asset.pk): str(asset.client_id) for asset in assets}
        self.fields["client"].required = False
        self.fields["asset"].required = False


class AssetDistributionForm(FirmScopedModelForm):
    """
    Simple form for creating one distribution record.

    It connects:
    - one asset
    - one beneficiary
    - one ownership percentage
    """

    class Meta:
        model = AssetDistribution
        fields = ["asset", "beneficiary", "ownership_percentage"]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)

        if user and user.firm_id:
            self.instance.firm = user.firm
            self.fields["asset"].queryset = Asset.objects.filter(firm=user.firm).order_by("title")
            self.fields["beneficiary"].queryset = Beneficiary.objects.filter(firm=user.firm).order_by(
                "last_name",
                "first_name",
            )

        # If a client id is passed in, keep the dropdowns limited to that client's records.
        client_id = self.initial.get("client")
        if client_id:
            self.fields["asset"].queryset = self.fields["asset"].queryset.filter(client_id=client_id)
            self.fields["beneficiary"].queryset = self.fields["beneficiary"].queryset.filter(
                client_id=client_id
            )

    def clean(self):
        cleaned_data = super().clean()
        asset = cleaned_data.get("asset")
        beneficiary = cleaned_data.get("beneficiary")

        # Basic form-level check so the user sees a clear message before model save.
        if asset and beneficiary and asset.client_id != beneficiary.client_id:
            raise ValidationError("Asset and beneficiary must belong to the same client.")

        return cleaned_data


class UserManagementForm(FirmScopedModelForm):
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"placeholder": "Temporary password"}),
        help_text="Leave blank when editing and you do not want to change the password.",
    )

    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "username",
            "email",
            "role",
            "is_senior_lawyer",
            "is_active",
        ]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields["is_senior_lawyer"].label = "Senior lawyer approval access"

    def save(self, commit=True):
        instance = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            instance.set_password(password)
        elif not instance.pk:
            instance.set_unusable_password()
        if self.user and self.user.firm_id and not instance.firm_id:
            instance.firm = self.user.firm
        if commit:
            instance.save()
        return instance


class AuditLogFilterForm(forms.Form):
    action = forms.CharField(required=False)
    actor = forms.ModelChoiceField(queryset=User.objects.none(), required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and user.firm_id:
            self.fields["actor"].queryset = User.objects.filter(firm=user.firm).order_by("username")


class ApprovalRejectForm(forms.Form):
    rejection_reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)


class TaskForm(FirmScopedModelForm):
    class Meta:
        model = Task
        fields = [
            "client",
            "title",
            "description",
            "assigned_to",
            "due_date",
            "priority",
            "status",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if user and user.firm_id:
            self.instance.firm = user.firm
            self.fields["client"].queryset = Client.objects.filter(firm=user.firm).order_by(
                "last_name",
                "first_name",
            )
            self.fields["assigned_to"].queryset = User.objects.filter(firm=user.firm).order_by(
                "first_name",
                "last_name",
                "username",
            )
        self.fields["client"].required = False


class CommentForm(FirmScopedModelForm):
    class Meta:
        model = Comment
        fields = ["text"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 3, "placeholder": "Add an internal comment..."}),
        }


class DocumentVersionForm(FirmScopedModelForm):
    class Meta:
        model = DocumentVersion
        fields = ["file", "note"]
        widgets = {
            "note": forms.Textarea(attrs={"rows": 3, "placeholder": "What changed in this version?"}),
        }
