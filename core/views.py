from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

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
from .permissions import ActionRolePermission
from .serializers import (
    ApprovalActionSerializer,
    AssetDistributionSerializer,
    AssetSerializer,
    AuditLogSerializer,
    BeneficiarySerializer,
    ClientAssignmentSerializer,
    ClientSerializer,
    DashboardSummarySerializer,
    DocumentSerializer,
    FirmSerializer,
    UserSerializer,
)


def create_audit_log(actor, action, instance, description="", metadata=None):
    firm = getattr(instance, "firm", None) or getattr(actor, "firm", None)
    if not firm:
        return

    AuditLog.objects.create(
        firm=firm,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        target_model=instance.__class__.__name__,
        target_id=instance.pk,
        description=description,
        metadata=metadata or {},
    )


class FirmScopedModelViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionRolePermission]
    search_fields = ()
    ordering_fields = "__all__"

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user.is_superuser:
            return queryset
        return queryset.filter(firm=user.firm)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    def get_create_kwargs(self):
        kwargs = {}
        user = self.request.user
        if not user.is_superuser and hasattr(self.queryset.model, "firm_id"):
            kwargs["firm"] = user.firm
        return kwargs

    def perform_create(self, serializer):
        instance = serializer.save(**self.get_create_kwargs())
        create_audit_log(self.request.user, "create", instance, description=f"Created {instance}.")

    def perform_update(self, serializer):
        instance = serializer.save()
        create_audit_log(self.request.user, "update", instance, description=f"Updated {instance}.")

    def perform_destroy(self, instance):
        target_label = str(instance)
        target_id = instance.pk
        model_name = instance.__class__.__name__
        firm = instance.firm
        instance.delete()
        AuditLog.objects.create(
            firm=firm,
            actor=self.request.user,
            action="delete",
            target_model=model_name,
            target_id=target_id,
            description=f"Deleted {target_label}.",
            metadata={},
        )

    def _ensure_client_is_editable(self, client):
        if (
            self.request.method not in permissions.SAFE_METHODS
            and client.status == Client.Status.CLOSED
            and not self.request.user.is_superuser
            and self.request.user.role != User.Role.ADMIN
        ):
            return Response(
                {"detail": "Closed estates are read-only unless reopened by an admin."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None


@extend_schema(tags=["Firms"])
class FirmViewSet(FirmScopedModelViewSet):
    queryset = Firm.objects.all()
    serializer_class = FirmSerializer
    action_role_map = {
        "list": {User.Role.ADMIN},
        "retrieve": {User.Role.ADMIN},
        "create": {User.Role.ADMIN},
        "update": {User.Role.ADMIN},
        "partial_update": {User.Role.ADMIN},
        "destroy": {User.Role.ADMIN},
    }
    search_fields = ("name",)


@extend_schema(tags=["Users"])
class UserViewSet(FirmScopedModelViewSet):
    queryset = User.objects.select_related("firm").all()
    serializer_class = UserSerializer
    action_role_map = {
        "list": {User.Role.ADMIN},
        "retrieve": {User.Role.ADMIN},
        "create": {User.Role.ADMIN},
        "update": {User.Role.ADMIN},
        "partial_update": {User.Role.ADMIN},
        "destroy": {User.Role.ADMIN},
    }
    search_fields = ("username", "first_name", "last_name", "email")

    def get_queryset(self):
        queryset = super().get_queryset()
        role = self.request.query_params.get("role")
        if role:
            queryset = queryset.filter(role=role)
        return queryset

    def get_create_kwargs(self):
        kwargs = super().get_create_kwargs()
        if self.request.user.is_superuser and "firm" not in self.request.data:
            kwargs["firm"] = self.request.user.firm
        return kwargs


@extend_schema(
    tags=["Clients"],
    parameters=[
        OpenApiParameter(name="status", description="Filter by client status."),
        OpenApiParameter(name="assigned_lawyer", description="Filter by assigned lawyer ID."),
        OpenApiParameter(name="search", description="Search by name, phone, or email."),
    ],
)
class ClientViewSet(FirmScopedModelViewSet):
    queryset = Client.objects.select_related("firm", "reopened_by").all()
    serializer_class = ClientSerializer
    action_role_map = {
        "list": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "retrieve": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "create": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "update": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "partial_update": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "destroy": {User.Role.ADMIN, User.Role.LAWYER},
        "reopen": {User.Role.ADMIN},
    }
    search_fields = ("first_name", "last_name", "email", "phone")

    def get_queryset(self):
        queryset = super().get_queryset()
        status_filter = self.request.query_params.get("status")
        assigned_lawyer = self.request.query_params.get("assigned_lawyer")
        search = self.request.query_params.get("search")

        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if assigned_lawyer:
            queryset = queryset.filter(assignments__lawyer_id=assigned_lawyer)
        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
            )
        return queryset.distinct()

    @extend_schema(request=None, responses=ClientSerializer)
    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        client = self.get_object()
        client.status = Client.Status.ESTATE_PROCESSING
        client.reopened_by = request.user
        client.reopened_at = timezone.now()
        client.full_clean()
        client.save(update_fields=["status", "reopened_by", "reopened_at", "updated_at"])
        create_audit_log(request.user, "reopen", client, description=f"Reopened estate for {client}.")
        return Response(self.get_serializer(client).data)


@extend_schema(tags=["Client Assignments"])
class ClientAssignmentViewSet(FirmScopedModelViewSet):
    queryset = ClientAssignment.objects.select_related("firm", "client", "lawyer", "assigned_by").all()
    serializer_class = ClientAssignmentSerializer
    action_role_map = {
        "list": {User.Role.ADMIN, User.Role.LAWYER},
        "retrieve": {User.Role.ADMIN, User.Role.LAWYER},
        "create": {User.Role.ADMIN},
        "update": {User.Role.ADMIN},
        "partial_update": {User.Role.ADMIN},
        "destroy": {User.Role.ADMIN},
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        client_id = self.request.query_params.get("client")
        lawyer_id = self.request.query_params.get("lawyer")
        if client_id:
            queryset = queryset.filter(client_id=client_id)
        if lawyer_id:
            queryset = queryset.filter(lawyer_id=lawyer_id)
        return queryset

    def get_create_kwargs(self):
        kwargs = super().get_create_kwargs()
        kwargs["assigned_by"] = self.request.user
        return kwargs

    def create(self, request, *args, **kwargs):
        client_id = request.data.get("client")
        if client_id:
            client = Client.objects.filter(pk=client_id).first()
            if client:
                blocked = self._ensure_client_is_editable(client)
                if blocked:
                    return blocked
        return super().create(request, *args, **kwargs)


@extend_schema(
    tags=["Beneficiaries"],
    parameters=[OpenApiParameter(name="client", description="Filter by client ID.")],
)
class BeneficiaryViewSet(FirmScopedModelViewSet):
    queryset = Beneficiary.objects.select_related("firm", "client").all()
    serializer_class = BeneficiarySerializer
    action_role_map = {
        "list": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "retrieve": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "create": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "update": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "partial_update": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "destroy": {User.Role.ADMIN, User.Role.LAWYER},
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        client_id = self.request.query_params.get("client")
        if client_id:
            queryset = queryset.filter(client_id=client_id)
        return queryset

    def create(self, request, *args, **kwargs):
        client = Client.objects.filter(pk=request.data.get("client")).first()
        if client:
            blocked = self._ensure_client_is_editable(client)
            if blocked:
                return blocked
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        blocked = self._ensure_client_is_editable(self.get_object().client)
        if blocked:
            return blocked
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        blocked = self._ensure_client_is_editable(self.get_object().client)
        if blocked:
            return blocked
        return super().partial_update(request, *args, **kwargs)


@extend_schema(
    tags=["Assets"],
    parameters=[
        OpenApiParameter(name="client", description="Filter by client ID."),
        OpenApiParameter(name="category", description="Filter by asset category."),
    ],
)
class AssetViewSet(FirmScopedModelViewSet):
    queryset = Asset.objects.select_related("firm", "client").all()
    serializer_class = AssetSerializer
    action_role_map = {
        "list": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "retrieve": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "create": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "update": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "partial_update": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "destroy": {User.Role.ADMIN, User.Role.LAWYER},
    }
    search_fields = ("title", "description")

    def get_queryset(self):
        queryset = super().get_queryset()
        client_id = self.request.query_params.get("client")
        category = self.request.query_params.get("category")
        if client_id:
            queryset = queryset.filter(client_id=client_id)
        if category:
            queryset = queryset.filter(category=category)
        return queryset

    def create(self, request, *args, **kwargs):
        client = Client.objects.filter(pk=request.data.get("client")).first()
        if client:
            blocked = self._ensure_client_is_editable(client)
            if blocked:
                return blocked
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        blocked = self._ensure_client_is_editable(self.get_object().client)
        if blocked:
            return blocked
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        blocked = self._ensure_client_is_editable(self.get_object().client)
        if blocked:
            return blocked
        return super().partial_update(request, *args, **kwargs)


@extend_schema(
    tags=["Asset Distributions"],
    parameters=[
        OpenApiParameter(name="asset", description="Filter by asset ID."),
        OpenApiParameter(name="approval_status", description="Filter by approval status."),
    ],
)
class AssetDistributionViewSet(FirmScopedModelViewSet):
    queryset = AssetDistribution.objects.select_related(
        "firm", "asset", "beneficiary", "approved_by", "asset__client"
    ).all()
    serializer_class = AssetDistributionSerializer
    action_role_map = {
        "list": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "retrieve": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "create": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "update": {User.Role.ADMIN, User.Role.LAWYER},
        "partial_update": {User.Role.ADMIN, User.Role.LAWYER},
        "destroy": {User.Role.ADMIN, User.Role.LAWYER},
        "approve": {"approver"},
        "reject": {"approver"},
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        asset_id = self.request.query_params.get("asset")
        approval_status = self.request.query_params.get("approval_status")
        if asset_id:
            queryset = queryset.filter(asset_id=asset_id)
        if approval_status:
            queryset = queryset.filter(approval_status=approval_status)
        return queryset

    def create(self, request, *args, **kwargs):
        asset = Asset.objects.filter(pk=request.data.get("asset")).select_related("client").first()
        if asset:
            blocked = self._ensure_client_is_editable(asset.client)
            if blocked:
                return blocked
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        blocked = self._ensure_client_is_editable(self.get_object().asset.client)
        if blocked:
            return blocked
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        blocked = self._ensure_client_is_editable(self.get_object().asset.client)
        if blocked:
            return blocked
        return super().partial_update(request, *args, **kwargs)

    @extend_schema(request=None, responses=AssetDistributionSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        distribution = self.get_object()
        blocked = self._ensure_client_is_editable(distribution.asset.client)
        if blocked:
            return blocked

        distribution.approval_status = AssetDistribution.ApprovalStatus.APPROVED
        distribution.rejection_reason = ""
        distribution.approved_by = request.user
        distribution.full_clean()
        distribution.save()
        create_audit_log(request.user, "approve", distribution, description=f"Approved distribution {distribution}.")
        return Response(self.get_serializer(distribution).data)

    @extend_schema(request=ApprovalActionSerializer, responses=AssetDistributionSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        distribution = self.get_object()
        blocked = self._ensure_client_is_editable(distribution.asset.client)
        if blocked:
            return blocked

        serializer = ApprovalActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        distribution.approval_status = AssetDistribution.ApprovalStatus.REJECTED
        distribution.rejection_reason = serializer.validated_data.get("rejection_reason", "")
        distribution.approved_by = None
        distribution.approved_at = None
        distribution.full_clean()
        distribution.save()
        create_audit_log(request.user, "reject", distribution, description=f"Rejected distribution {distribution}.")
        return Response(self.get_serializer(distribution).data)


@extend_schema(
    tags=["Documents"],
    parameters=[
        OpenApiParameter(name="client", description="Filter by client ID."),
        OpenApiParameter(name="asset", description="Filter by asset ID."),
    ],
)
class DocumentViewSet(FirmScopedModelViewSet):
    queryset = Document.objects.select_related("firm", "client", "asset", "uploaded_by").all()
    serializer_class = DocumentSerializer
    parser_classes = (MultiPartParser, FormParser)
    action_role_map = {
        "list": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "retrieve": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "create": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "update": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "partial_update": {User.Role.ADMIN, User.Role.LAWYER, User.Role.INTERN},
        "destroy": {User.Role.ADMIN, User.Role.LAWYER},
    }
    search_fields = ("title",)

    def get_queryset(self):
        queryset = super().get_queryset()
        client_id = self.request.query_params.get("client")
        asset_id = self.request.query_params.get("asset")
        if client_id:
            queryset = queryset.filter(client_id=client_id)
        if asset_id:
            queryset = queryset.filter(asset_id=asset_id)
        return queryset

    def get_create_kwargs(self):
        kwargs = super().get_create_kwargs()
        kwargs["uploaded_by"] = self.request.user
        return kwargs

    def create(self, request, *args, **kwargs):
        client_id = request.data.get("client")
        asset_id = request.data.get("asset")
        client = Client.objects.filter(pk=client_id).first() if client_id else None
        asset = Asset.objects.filter(pk=asset_id).select_related("client").first() if asset_id else None

        target_client = client or getattr(asset, "client", None)
        if target_client:
            blocked = self._ensure_client_is_editable(target_client)
            if blocked:
                return blocked
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        document = self.get_object()
        target_client = document.client or getattr(document.asset, "client", None)
        if target_client:
            blocked = self._ensure_client_is_editable(target_client)
            if blocked:
                return blocked
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        document = self.get_object()
        target_client = document.client or getattr(document.asset, "client", None)
        if target_client:
            blocked = self._ensure_client_is_editable(target_client)
            if blocked:
                return blocked
        return super().partial_update(request, *args, **kwargs)


@extend_schema(tags=["Audit Logs"])
class AuditLogViewSet(FirmScopedModelViewSet):
    queryset = AuditLog.objects.select_related("firm", "actor").all()
    serializer_class = AuditLogSerializer
    http_method_names = ["get", "head", "options"]
    action_role_map = {
        "list": {User.Role.ADMIN, User.Role.LAWYER},
        "retrieve": {User.Role.ADMIN, User.Role.LAWYER},
    }


@extend_schema(tags=["Dashboard"], responses=DashboardSummarySerializer)
class DashboardSummaryAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        clients = Client.objects.all()
        assets = Asset.objects.all()
        distributions = AssetDistribution.objects.all()

        if not request.user.is_superuser:
            clients = clients.filter(firm=request.user.firm)
            assets = assets.filter(firm=request.user.firm)
            distributions = distributions.filter(firm=request.user.firm)

        data = {
            "total_clients": clients.count(),
            "total_assets": assets.count(),
            "active_estates": clients.exclude(status=Client.Status.CLOSED).count(),
            "pending_approvals": distributions.filter(
                approval_status=AssetDistribution.ApprovalStatus.PENDING
            ).count(),
        }
        serializer = DashboardSummarySerializer(data)
        return Response(serializer.data)
