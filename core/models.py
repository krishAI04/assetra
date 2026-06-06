import os
import uuid
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Firm(TimeStampedModel):
    name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        LAWYER = "lawyer", "Lawyer"
        INTERN = "intern", "Intern"

    firm = models.ForeignKey(
        Firm,
        on_delete=models.CASCADE,
        related_name="users",
        null=True,
        blank=True,
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    is_senior_lawyer = models.BooleanField(default=False)

    class Meta:
        ordering = ["username"]

    def clean(self):
        super().clean()
        if self.role != self.Role.LAWYER and self.is_senior_lawyer:
            raise ValidationError("Only lawyers can be marked as senior lawyers.")

    @property
    def can_approve(self):
        return self.role == self.Role.ADMIN or (
            self.role == self.Role.LAWYER and self.is_senior_lawyer
        )

    @property
    def can_finalize(self):
        return self.role in {self.Role.ADMIN, self.Role.LAWYER}

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"


class Client(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DECEASED = "deceased", "Deceased"
        ESTATE_PROCESSING = "estate_processing", "Estate Processing"
        CLOSED = "closed", "Closed"

    firm = models.ForeignKey(Firm, on_delete=models.CASCADE, related_name="clients")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    date_of_death = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    reopened_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reopened_clients",
    )
    reopened_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        indexes = [
            models.Index(fields=["firm", "status"]),
            models.Index(fields=["firm", "last_name", "first_name"]),
            models.Index(fields=["firm", "email"]),
            models.Index(fields=["firm", "phone"]),
        ]

    def clean(self):
        super().clean()
        if self.status == self.Status.CLOSED and self.reopened_at and not self.reopened_by:
            raise ValidationError("A reopened client must record the admin who reopened it.")

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self):
        return self.full_name


class ClientAssignment(TimeStampedModel):
    firm = models.ForeignKey(
        Firm,
        on_delete=models.CASCADE,
        related_name="client_assignments",
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    lawyer = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="client_assignments",
    )
    assigned_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_client_assignments",
    )

    class Meta:
        unique_together = ("client", "lawyer")
        ordering = ["client", "lawyer"]

    def clean(self):
        super().clean()
        if self.lawyer.role != User.Role.LAWYER:
            raise ValidationError("Only users with the lawyer role can be assigned to clients.")
        if self.client.firm_id != self.firm_id or self.lawyer.firm_id != self.firm_id:
            raise ValidationError("Assignments must stay within the same firm.")

    def __str__(self):
        return f"{self.lawyer} -> {self.client}"


class Beneficiary(TimeStampedModel):
    firm = models.ForeignKey(
        Firm,
        on_delete=models.CASCADE,
        related_name="beneficiaries",
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="beneficiaries",
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    relationship_to_client = models.CharField(max_length=100)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        indexes = [models.Index(fields=["firm", "client"])]

    def clean(self):
        super().clean()
        if self.client.firm_id != self.firm_id:
            raise ValidationError("Beneficiaries must belong to the same firm as the client.")

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()


class Asset(TimeStampedModel):
    class Category(models.TextChoices):
        PROPERTY = "property", "Property"
        BANK = "bank", "Bank"
        STOCK = "stock", "Stock"
        VEHICLE = "vehicle", "Vehicle"
        INSURANCE = "insurance", "Insurance"
        BUSINESS = "business", "Business"
        OTHER = "other", "Other"

    firm = models.ForeignKey(Firm, on_delete=models.CASCADE, related_name="assets")
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="assets")
    title = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=Category.choices)
    description = models.TextField(blank=True)
    estimated_value = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["title"]
        indexes = [models.Index(fields=["firm", "client", "category"])]

    def clean(self):
        super().clean()
        if self.client.firm_id != self.firm_id:
            raise ValidationError("Assets must belong to the same firm as the client.")

    def __str__(self):
        return self.title


class AssetDistribution(TimeStampedModel):
    class ApprovalStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    firm = models.ForeignKey(
        Firm,
        on_delete=models.CASCADE,
        related_name="asset_distributions",
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="distributions",
    )
    beneficiary = models.ForeignKey(
        Beneficiary,
        on_delete=models.CASCADE,
        related_name="asset_distributions",
    )
    ownership_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    approval_status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
    )
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_asset_distributions",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        unique_together = ("asset", "beneficiary")
        ordering = ["asset", "beneficiary"]
        indexes = [models.Index(fields=["firm", "approval_status"])]

    def clean(self):
        super().clean()
        if self.asset.firm_id != self.firm_id or self.beneficiary.firm_id != self.firm_id:
            raise ValidationError("Distributions must stay within the same firm.")
        if self.asset.client_id != self.beneficiary.client_id:
            raise ValidationError("Beneficiary and asset must belong to the same client.")
        if self.ownership_percentage <= 0 or self.ownership_percentage > 100:
            raise ValidationError("Ownership percentage must be greater than 0 and at most 100.")

        existing_total = (
            AssetDistribution.objects.filter(asset=self.asset)
            .exclude(pk=self.pk)
            .aggregate(total=Sum("ownership_percentage"))
            .get("total")
            or Decimal("0.00")
        )
        if existing_total + self.ownership_percentage > Decimal("100.00"):
            raise ValidationError("Total ownership percentage for an asset cannot exceed 100.")

        if self.approval_status == self.ApprovalStatus.APPROVED:
            if not self.approved_by:
                raise ValidationError("Approved distributions must record the approver.")
            if not self.approved_by.can_approve:
                raise ValidationError("Only an admin or senior lawyer can approve distributions.")
            if not self.approved_at:
                self.approved_at = timezone.now()
        elif self.approved_by or self.approved_at:
            raise ValidationError(
                "Approver details can only be set when the distribution is approved."
            )

    def __str__(self):
        return f"{self.asset} -> {self.beneficiary} ({self.ownership_percentage}%)"


def document_upload_to(instance, filename):
    """
    Store uploaded documents using randomized names to avoid exposing
    original filenames or predictable paths.
    """
    _, extension = os.path.splitext(filename or "")
    randomized_name = f"{uuid.uuid4().hex}{extension.lower()}"
    return os.path.join("documents", randomized_name)


class Document(TimeStampedModel):
    firm = models.ForeignKey(Firm, on_delete=models.CASCADE, related_name="documents")
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="documents",
        null=True,
        blank=True,
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="documents",
        null=True,
        blank=True,
    )
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="uploaded_documents",
    )
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to=document_upload_to)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["firm", "created_at"])]

    def clean(self):
        super().clean()
        if not self.client and not self.asset:
            raise ValidationError("A document must be attached to a client or an asset.")
        if self.client and self.client.firm_id != self.firm_id:
            raise ValidationError("Client documents must stay within the same firm.")
        if self.asset and self.asset.firm_id != self.firm_id:
            raise ValidationError("Asset documents must stay within the same firm.")
        if self.client and self.asset and self.asset.client_id != self.client_id:
            raise ValidationError("Asset documents must belong to the selected client.")

    def __str__(self):
        return self.title


class AuditLog(models.Model):
    firm = models.ForeignKey(Firm, on_delete=models.CASCADE, related_name="audit_logs")
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=50)
    target_model = models.CharField(max_length=100)
    target_id = models.PositiveBigIntegerField()
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["firm", "created_at"]),
            models.Index(fields=["target_model", "target_id"]),
        ]

    def __str__(self):
        return f"{self.action} {self.target_model}#{self.target_id}"


class Notification(TimeStampedModel):
    class Type(models.TextChoices):
        INFO = "info", "Info"
        CLIENT = "client", "Client"
        DOCUMENT = "document", "Document"
        APPROVAL = "approval", "Approval"
        USER = "user", "User"

    firm = models.ForeignKey(Firm, on_delete=models.CASCADE, related_name="notifications")
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    message = models.CharField(max_length=255)
    notification_type = models.CharField(
        max_length=20,
        choices=Type.choices,
        default=Type.INFO,
    )
    target_url = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["firm", "is_read", "created_at"]),
            models.Index(fields=["user", "is_read", "created_at"]),
        ]

    def __str__(self):
        return self.message
