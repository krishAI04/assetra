from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import (
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


class FullCleanModelSerializer(serializers.ModelSerializer):
    def _raise_validation_error(self, error):
        if hasattr(error, "message_dict"):
            raise serializers.ValidationError(error.message_dict)
        raise serializers.ValidationError(error.messages)

    def create(self, validated_data):
        instance = self.Meta.model(**validated_data)
        try:
            instance.full_clean()
        except DjangoValidationError as error:
            self._raise_validation_error(error)
        instance.save()
        return instance

    def update(self, instance, validated_data):
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        try:
            instance.full_clean()
        except DjangoValidationError as error:
            self._raise_validation_error(error)
        instance.save()
        return instance


class FirmSerializer(FullCleanModelSerializer):
    class Meta:
        model = Firm
        fields = ("id", "name", "is_active", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")


class UserSerializer(FullCleanModelSerializer):
    firm_name = serializers.CharField(source="firm.name", read_only=True)
    can_approve = serializers.BooleanField(read_only=True)
    can_finalize = serializers.BooleanField(read_only=True)
    password = serializers.CharField(write_only=True, required=False, style={"input_type": "password"})

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "password",
            "firm",
            "firm_name",
            "role",
            "is_senior_lawyer",
            "is_active",
            "can_approve",
            "can_finalize",
            "date_joined",
        )
        read_only_fields = ("id", "date_joined", "firm_name", "can_approve", "can_finalize")

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        instance = self.Meta.model(**validated_data)
        if password:
            instance.set_password(password)
        else:
            instance.set_unusable_password()
        try:
            instance.full_clean()
        except DjangoValidationError as error:
            self._raise_validation_error(error)
        instance.save()
        return instance

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        try:
            instance.full_clean()
        except DjangoValidationError as error:
            self._raise_validation_error(error)
        instance.save()
        return instance


class ClientSerializer(FullCleanModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Client
        fields = (
            "id",
            "firm",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "phone",
            "address",
            "status",
            "date_of_death",
            "notes",
            "reopened_by",
            "reopened_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "reopened_by", "reopened_at", "created_at", "updated_at", "full_name")


class ClientAssignmentSerializer(FullCleanModelSerializer):
    lawyer_name = serializers.CharField(source="lawyer.get_full_name", read_only=True)
    client_name = serializers.CharField(source="client.full_name", read_only=True)

    class Meta:
        model = ClientAssignment
        fields = (
            "id",
            "firm",
            "client",
            "client_name",
            "lawyer",
            "lawyer_name",
            "assigned_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "assigned_by", "created_at", "updated_at", "client_name", "lawyer_name")


class BeneficiarySerializer(FullCleanModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = Beneficiary
        fields = (
            "id",
            "firm",
            "client",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "phone",
            "relationship_to_client",
            "notes",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at", "full_name")

    def get_full_name(self, obj) -> str:
        return f"{obj.first_name} {obj.last_name}".strip()


class AssetSerializer(FullCleanModelSerializer):
    client_name = serializers.CharField(source="client.full_name", read_only=True)

    class Meta:
        model = Asset
        fields = (
            "id",
            "firm",
            "client",
            "client_name",
            "title",
            "category",
            "description",
            "estimated_value",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at", "client_name")


class AssetDistributionSerializer(FullCleanModelSerializer):
    asset_title = serializers.CharField(source="asset.title", read_only=True)
    beneficiary_name = serializers.SerializerMethodField()
    approved_by_name = serializers.CharField(source="approved_by.get_full_name", read_only=True)

    class Meta:
        model = AssetDistribution
        fields = (
            "id",
            "firm",
            "asset",
            "asset_title",
            "beneficiary",
            "beneficiary_name",
            "ownership_percentage",
            "approval_status",
            "approved_by",
            "approved_by_name",
            "approved_at",
            "rejection_reason",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "approved_by",
            "approved_by_name",
            "approved_at",
            "created_at",
            "updated_at",
            "asset_title",
            "beneficiary_name",
        )

    def get_beneficiary_name(self, obj) -> str:
        return f"{obj.beneficiary.first_name} {obj.beneficiary.last_name}".strip()


class DocumentSerializer(FullCleanModelSerializer):
    uploaded_by_name = serializers.CharField(source="uploaded_by.get_full_name", read_only=True)

    class Meta:
        model = Document
        fields = (
            "id",
            "firm",
            "client",
            "asset",
            "uploaded_by",
            "uploaded_by_name",
            "title",
            "file",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "uploaded_by", "uploaded_by_name", "created_at", "updated_at")


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.get_full_name", read_only=True)

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "firm",
            "actor",
            "actor_name",
            "action",
            "target_model",
            "target_id",
            "description",
            "metadata",
            "created_at",
        )
        read_only_fields = fields


class ApprovalActionSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField(required=False, allow_blank=True)


class DashboardSummarySerializer(serializers.Serializer):
    total_clients = serializers.IntegerField()
    total_assets = serializers.IntegerField()
    active_estates = serializers.IntegerField()
    pending_approvals = serializers.IntegerField()
