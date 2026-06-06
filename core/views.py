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


# ---------------------------------------------------------------------------
# Shared helper function
# ---------------------------------------------------------------------------

def create_audit_log(actor, action, instance, description="", metadata=None):
    """
    Create an audit log entry for any important change in the system.

    We try to get the firm from the changed object first.
    If that is not available, we fall back to the actor's firm.
    """
    firm = getattr(instance, "firm", None) or getattr(actor, "firm", None)

    # If we still do not know the firm, skip logging.
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


# ---------------------------------------------------------------------------
# Base API viewset used by most resources
# ---------------------------------------------------------------------------

class FirmScopedModelViewSet(viewsets.ModelViewSet):
    """
    Base viewset with shared behavior for all firm-owned models.

    Responsibilities:
    - require authentication
    - apply action-based role permissions
    - limit data to the logged-in user's firm
    - add audit logs on create/update/delete
    - block edits to closed estates for non-admins
    """

    permission_classes = [permissions.IsAuthenticated, ActionRolePermission]
    search_fields = ()
    ordering_fields = "__all__"

    def get_queryset(self):
        """
        Superusers can see everything.
        Other users can only see records from their own firm.
        """
        queryset = super().get_queryset()
        user = self.request.user

        if user.is_superuser:
            return queryset

        return queryset.filter(firm=user.firm)

    def get_serializer_context(self):
        """
        Add request into serializer context.
        This is useful when serializers need the current user/request.
        """
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    def get_create_kwargs(self):
        """
        Extra values passed into serializer.save() during create.

        For firm-owned models, we automatically attach the current user's firm
        unless the user is a superuser.
        """
        kwargs = {}
        user = self.request.user

        if not user.is_superuser and hasattr(self.queryset.model, "firm_id"):
            kwargs["firm"] = user.firm

        return kwargs

    def perform_create(self, serializer):
        """Save the new object and add an audit log entry."""
        instance = serializer.save(**self.get_create_kwargs())
        create_audit_log(
            self.request.user,
            "create",
            instance,
            description=f"Created {instance}.",
        )

    def perform_update(self, serializer):
        """Save updates and add an audit log entry."""
        instance = serializer.save()
        create_audit_log(
            self.request.user,
            "update",
            instance,
            description=f"Updated {instance}.",
        )

    def perform_destroy(self, instance):
        """Delete the object and write the delete action into the audit log."""
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

    def block_if_client_is_closed(self, client):
        """
        Stop write operations when the related client/estate is closed.

        Only admins and superusers are allowed to modify closed estates.
        """
        if not client:
            return None

        is_write_request = self.request.method not in permissions.SAFE_METHODS
        is_closed_client = client.status == Client.Status.CLOSED
        is_admin_user = self.request.user.is_superuser or self.request.user.role == User.Role.ADMIN

        if is_write_request and is_closed_client and not is_admin_user:
            return Response(
                {"detail": "Closed estates are read-only unless reopened by an admin."},
                status=status.HTTP_403_FORBIDDEN,
            )

        return None


# ---------------------------------------------------------------------------
# Firm endpoints
# ---------------------------------------------------------------------------

@extend_schema(tags=["Firms"])
class FirmViewSet(FirmScopedModelViewSet):
    """API endpoints for firms."""

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


# ---------------------------------------------------------------------------
# User endpoints
# ---------------------------------------------------------------------------

@extend_schema(tags=["Users"])
class UserViewSet(FirmScopedModelViewSet):
    """API endpoints for firm users."""

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
        """Optionally filter users by role."""
        queryset = super().get_queryset()
        role = self.request.query_params.get("role")

        if role:
            queryset = queryset.filter(role=role)

        return queryset

    def get_create_kwargs(self):
        """
        Keep the default firm assignment behavior.

        If a superuser creates a user and no firm is provided,
        attach the superuser's own firm.
        """
        kwargs = super().get_create_kwargs()

        if self.request.user.is_superuser and "firm" not in self.request.data:
            kwargs["firm"] = self.request.user.firm

        return kwargs


# ---------------------------------------------------------------------------
# Client endpoints
# ---------------------------------------------------------------------------

@extend_schema(
    tags=["Clients"],
    parameters=[
        OpenApiParameter(name="status", description="Filter by client status."),
        OpenApiParameter(name="assigned_lawyer", description="Filter by assigned lawyer ID."),
        OpenApiParameter(name="search", description="Search by name, phone, or email."),
    ],
)
class ClientViewSet(FirmScopedModelViewSet):
    """API endpoints for clients/estates."""

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
        """Apply list filters for client pages."""
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
        """Reopen a closed estate and record who reopened it."""
        client = self.get_object()
        client.status = Client.Status.ESTATE_PROCESSING
        client.reopened_by = request.user
        client.reopened_at = timezone.now()
        client.full_clean()
        client.save(update_fields=["status", "reopened_by", "reopened_at", "updated_at"])

        create_audit_log(
            request.user,
            "reopen",
            client,
            description=f"Reopened estate for {client}.",
        )

        return Response(self.get_serializer(client).data)


# ---------------------------------------------------------------------------
# Client assignment endpoints
# ---------------------------------------------------------------------------

@extend_schema(tags=["Client Assignments"])
class ClientAssignmentViewSet(FirmScopedModelViewSet):
    """API endpoints for assigning lawyers to clients."""

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
        """Optionally filter assignments by client or lawyer."""
        queryset = super().get_queryset()
        client_id = self.request.query_params.get("client")
        lawyer_id = self.request.query_params.get("lawyer")

        if client_id:
            queryset = queryset.filter(client_id=client_id)

        if lawyer_id:
            queryset = queryset.filter(lawyer_id=lawyer_id)

        return queryset

    def get_create_kwargs(self):
        """Store the user who created the assignment."""
        kwargs = super().get_create_kwargs()
        kwargs["assigned_by"] = self.request.user
        return kwargs

    def create(self, request, *args, **kwargs):
        """Block assignment changes when the target client is closed."""
        client_id = request.data.get("client")
        client = Client.objects.filter(pk=client_id).first() if client_id else None

        blocked = self.block_if_client_is_closed(client)
        if blocked:
            return blocked

        return super().create(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Beneficiary endpoints
# ---------------------------------------------------------------------------

@extend_schema(
    tags=["Beneficiaries"],
    parameters=[OpenApiParameter(name="client", description="Filter by client ID.")],
)
class BeneficiaryViewSet(FirmScopedModelViewSet):
    """API endpoints for beneficiaries."""

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
        """Optionally filter beneficiaries by client."""
        queryset = super().get_queryset()
        client_id = self.request.query_params.get("client")

        if client_id:
            queryset = queryset.filter(client_id=client_id)

        return queryset

    def create(self, request, *args, **kwargs):
        """Block beneficiary creation when the related client is closed."""
        client = Client.objects.filter(pk=request.data.get("client")).first()
        blocked = self.block_if_client_is_closed(client)

        if blocked:
            return blocked

        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        """Block beneficiary updates when the related client is closed."""
        blocked = self.block_if_client_is_closed(self.get_object().client)

        if blocked:
            return blocked

        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        """Block partial beneficiary updates when the related client is closed."""
        blocked = self.block_if_client_is_closed(self.get_object().client)

        if blocked:
            return blocked

        return super().partial_update(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Asset endpoints
# ---------------------------------------------------------------------------

@extend_schema(
    tags=["Assets"],
    parameters=[
        OpenApiParameter(name="client", description="Filter by client ID."),
        OpenApiParameter(name="category", description="Filter by asset category."),
    ],
)
class AssetViewSet(FirmScopedModelViewSet):
    """API endpoints for assets."""

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
        """Optionally filter assets by client or category."""
        queryset = super().get_queryset()
        client_id = self.request.query_params.get("client")
        category = self.request.query_params.get("category")

        if client_id:
            queryset = queryset.filter(client_id=client_id)

        if category:
            queryset = queryset.filter(category=category)

        return queryset

    def create(self, request, *args, **kwargs):
        """Block asset creation when the related client is closed."""
        client = Client.objects.filter(pk=request.data.get("client")).first()
        blocked = self.block_if_client_is_closed(client)

        if blocked:
            return blocked

        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        """Block asset updates when the related client is closed."""
        blocked = self.block_if_client_is_closed(self.get_object().client)

        if blocked:
            return blocked

        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        """Block partial asset updates when the related client is closed."""
        blocked = self.block_if_client_is_closed(self.get_object().client)

        if blocked:
            return blocked

        return super().partial_update(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Asset distribution endpoints
# ---------------------------------------------------------------------------

@extend_schema(
    tags=["Asset Distributions"],
    parameters=[
        OpenApiParameter(name="asset", description="Filter by asset ID."),
        OpenApiParameter(name="approval_status", description="Filter by approval status."),
    ],
)
class AssetDistributionViewSet(FirmScopedModelViewSet):
    """API endpoints for asset distribution records."""

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
        """Optionally filter distributions by asset or approval status."""
        queryset = super().get_queryset()
        asset_id = self.request.query_params.get("asset")
        approval_status = self.request.query_params.get("approval_status")

        if asset_id:
            queryset = queryset.filter(asset_id=asset_id)

        if approval_status:
            queryset = queryset.filter(approval_status=approval_status)

        return queryset

    def create(self, request, *args, **kwargs):
        """Block distribution creation when the related asset's client is closed."""
        asset = Asset.objects.filter(pk=request.data.get("asset")).select_related("client").first()
        client = asset.client if asset else None

        blocked = self.block_if_client_is_closed(client)
        if blocked:
            return blocked

        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        """Block distribution updates when the related client is closed."""
        blocked = self.block_if_client_is_closed(self.get_object().asset.client)

        if blocked:
            return blocked

        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        """Block partial distribution updates when the related client is closed."""
        blocked = self.block_if_client_is_closed(self.get_object().asset.client)

        if blocked:
            return blocked

        return super().partial_update(request, *args, **kwargs)

    @extend_schema(request=None, responses=AssetDistributionSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Approve a pending distribution."""
        distribution = self.get_object()
        blocked = self.block_if_client_is_closed(distribution.asset.client)

        if blocked:
            return blocked

        distribution.approval_status = AssetDistribution.ApprovalStatus.APPROVED
        distribution.rejection_reason = ""
        distribution.approved_by = request.user
        distribution.full_clean()
        distribution.save()

        create_audit_log(
            request.user,
            "approve",
            distribution,
            description=f"Approved distribution {distribution}.",
        )

        return Response(self.get_serializer(distribution).data)

    @extend_schema(request=ApprovalActionSerializer, responses=AssetDistributionSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Reject a pending distribution and optionally store a rejection reason."""
        distribution = self.get_object()
        blocked = self.block_if_client_is_closed(distribution.asset.client)

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

        create_audit_log(
            request.user,
            "reject",
            distribution,
            description=f"Rejected distribution {distribution}.",
        )

        return Response(self.get_serializer(distribution).data)


# ---------------------------------------------------------------------------
# Document endpoints
# ---------------------------------------------------------------------------

@extend_schema(
    tags=["Documents"],
    parameters=[
        OpenApiParameter(name="client", description="Filter by client ID."),
        OpenApiParameter(name="asset", description="Filter by asset ID."),
    ],
)
class DocumentViewSet(FirmScopedModelViewSet):
    """API endpoints for uploaded documents."""

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
        """Optionally filter documents by client or asset."""
        queryset = super().get_queryset()
        client_id = self.request.query_params.get("client")
        asset_id = self.request.query_params.get("asset")

        if client_id:
            queryset = queryset.filter(client_id=client_id)

        if asset_id:
            queryset = queryset.filter(asset_id=asset_id)

        return queryset

    def get_create_kwargs(self):
        """Automatically save the current user as the uploader."""
        kwargs = super().get_create_kwargs()
        kwargs["uploaded_by"] = self.request.user
        return kwargs

    def create(self, request, *args, **kwargs):
        """Block upload when the related client/asset belongs to a closed estate."""
        client_id = request.data.get("client")
        asset_id = request.data.get("asset")

        client = Client.objects.filter(pk=client_id).first() if client_id else None
        asset = Asset.objects.filter(pk=asset_id).select_related("client").first() if asset_id else None
        target_client = client or getattr(asset, "client", None)

        blocked = self.block_if_client_is_closed(target_client)
        if blocked:
            return blocked

        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        """Block document updates when the related client is closed."""
        document = self.get_object()
        target_client = document.client or getattr(document.asset, "client", None)
        blocked = self.block_if_client_is_closed(target_client)

        if blocked:
            return blocked

        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        """Block partial document updates when the related client is closed."""
        document = self.get_object()
        target_client = document.client or getattr(document.asset, "client", None)
        blocked = self.block_if_client_is_closed(target_client)

        if blocked:
            return blocked

        return super().partial_update(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Audit log endpoints
# ---------------------------------------------------------------------------

@extend_schema(tags=["Audit Logs"])
class AuditLogViewSet(FirmScopedModelViewSet):
    """Read-only API endpoints for audit logs."""

    queryset= AuditLog.objects.select_related("firm", "actor").all()
    serializer_class = AuditLogSerializer
    http_method_names = ["get", "head", "options"]
    
    
        
    action_role_map = {
            "list": {User.Role.ADMIN, User.Role.LAWYER},
            "retrieve": {User.Role.ADMIN, User.Role.LAWYER},
        }
   

# ---------------------------------------------------------------------------
# Dashboard summary endpoint
# ---------------------------------------------------------------------------

@extend_schema(tags=["Dashboard"], responses=DashboardSummarySerializer)
class DashboardSummaryAPIView(APIView):
    """Small API endpoint used to show dashboard summary numbers."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Return top-level dashboard counts for the current user's firm."""
        clients = Client.objects.all()
        assets = Asset.objects.all()
        distributions = AssetDistribution.objects.all()

        # Non-superusers only see numbers from their own firm.
        if not request.user.is_superuser:
            clients = clients.filter(firm=request.user.firm)
            assets = assets.filter(firm=request.user.firm)
            distributions = distributions.filter(firm=request.user.firm)

        summary = {
            "total_clients": clients.count(),
            "total_assets": assets.count(),
            "active_estates": clients.exclude(status=Client.Status.CLOSED).count(),
            "pending_approvals": distributions.filter(
                approval_status=AssetDistribution.ApprovalStatus.PENDING
            ).count(),
        }

        serializer = DashboardSummarySerializer(instance=summary)
        return Response(serializer.data)
