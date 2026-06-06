from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    AIDocumentAnalysis,
    Asset,
    AssetDistribution,
    AuditLog,
    Beneficiary,
    Client,
    ClientAssignment,
    Document,
    Firm,
    User,
)


admin.site.site_header = "Assetra Administration"
admin.site.site_title = "Assetra Admin"
admin.site.index_title = "Law Firm Operations"


class ClientAssignmentInline(admin.TabularInline):
    model = ClientAssignment
    extra = 0
    autocomplete_fields = ("lawyer", "assigned_by")


class BeneficiaryInline(admin.TabularInline):
    model = Beneficiary
    extra = 0


class AssetInline(admin.TabularInline):
    model = Asset
    extra = 0


class DocumentInline(admin.TabularInline):
    model = Document
    extra = 0
    autocomplete_fields = ("uploaded_by",)
    fields = ("title", "file", "uploaded_by", "created_at")
    readonly_fields = ("created_at",)


class AssetDistributionInline(admin.TabularInline):
    model = AssetDistribution
    extra = 0
    autocomplete_fields = ("beneficiary", "approved_by")
    readonly_fields = ("approved_at", "created_at", "updated_at")


@admin.register(Firm)
class FirmAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    search_fields = ("name",)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "firm", "role", "is_senior_lawyer", "is_active")
    list_filter = ("role", "is_senior_lawyer", "firm", "is_active")
    search_fields = ("username", "first_name", "last_name", "email")
    list_select_related = ("firm",)
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Assetra Access", {"fields": ("firm", "role", "is_senior_lawyer")}),
    )
    autocomplete_fields = ("firm",)


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("full_name", "firm", "status", "email", "phone", "updated_at")
    list_filter = ("status", "firm")
    search_fields = ("first_name", "last_name", "email", "phone")
    list_select_related = ("firm", "reopened_by")
    readonly_fields = ("reopened_by", "reopened_at", "created_at", "updated_at")
    inlines = (ClientAssignmentInline, BeneficiaryInline, AssetInline, DocumentInline)


@admin.register(ClientAssignment)
class ClientAssignmentAdmin(admin.ModelAdmin):
    list_display = ("client", "lawyer", "firm", "assigned_by", "created_at")
    list_filter = ("firm",)
    list_select_related = ("client", "lawyer", "firm", "assigned_by")
    autocomplete_fields = ("client", "lawyer", "assigned_by", "firm")


@admin.register(Beneficiary)
class BeneficiaryAdmin(admin.ModelAdmin):
    list_display = ("first_name", "last_name", "client", "relationship_to_client", "firm")
    list_filter = ("firm",)
    search_fields = ("first_name", "last_name", "email", "phone")
    list_select_related = ("client", "firm")
    autocomplete_fields = ("client", "firm")


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("title", "client", "category", "estimated_value", "firm", "is_active")
    list_filter = ("category", "firm", "is_active")
    search_fields = ("title", "description")
    list_select_related = ("client", "firm")
    autocomplete_fields = ("client", "firm")
    inlines = (AssetDistributionInline, DocumentInline)


@admin.register(AssetDistribution)
class AssetDistributionAdmin(admin.ModelAdmin):
    list_display = (
        "asset",
        "beneficiary",
        "ownership_percentage",
        "approval_status",
        "approved_by",
        "approved_at",
    )
    list_filter = ("approval_status", "firm")
    list_select_related = ("asset", "beneficiary", "approved_by", "firm")
    autocomplete_fields = ("asset", "beneficiary", "approved_by", "firm")
    readonly_fields = ("approved_at", "created_at", "updated_at")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "firm", "client", "asset", "uploaded_by", "created_at")
    list_filter = ("firm",)
    search_fields = ("title",)
    list_select_related = ("firm", "client", "asset", "uploaded_by")
    autocomplete_fields = ("firm", "client", "asset", "uploaded_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "target_model", "target_id", "firm", "actor", "created_at")
    list_filter = ("firm", "action", "target_model")
    search_fields = ("description",)
    list_select_related = ("firm", "actor")
    readonly_fields = ("firm", "actor", "action", "target_model", "target_id", "description", "metadata", "created_at")


@admin.register(AIDocumentAnalysis)
class AIDocumentAnalysisAdmin(admin.ModelAdmin):
    list_display = ("document", "firm", "mode", "created_by", "created_at")
    list_filter = ("firm", "mode")
    search_fields = ("document__title", "summary")
    list_select_related = ("firm", "document", "created_by")
    readonly_fields = (
        "firm",
        "document",
        "created_by",
        "mode",
        "summary",
        "important_parties",
        "important_dates",
        "asset_details",
        "risk_points",
        "suggested_next_action",
        "raw_response",
        "created_at",
        "updated_at",
    )
