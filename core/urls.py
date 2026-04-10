from rest_framework.routers import DefaultRouter
from django.urls import path

from .views import (
    AssetDistributionViewSet,
    AssetViewSet,
    AuditLogViewSet,
    BeneficiaryViewSet,
    ClientAssignmentViewSet,
    ClientViewSet,
    DashboardSummaryAPIView,
    DocumentViewSet,
    FirmViewSet,
    UserViewSet,
)

router = DefaultRouter()
router.register("firms", FirmViewSet, basename="firm")
router.register("users", UserViewSet, basename="user")
router.register("clients", ClientViewSet, basename="client")
router.register("client-assignments", ClientAssignmentViewSet, basename="client-assignment")
router.register("beneficiaries", BeneficiaryViewSet, basename="beneficiary")
router.register("assets", AssetViewSet, basename="asset")
router.register("asset-distributions", AssetDistributionViewSet, basename="asset-distribution")
router.register("documents", DocumentViewSet, basename="document")
router.register("audit-logs", AuditLogViewSet, basename="audit-log")

urlpatterns = [
    path("dashboard/summary/", DashboardSummaryAPIView.as_view(), name="dashboard-summary"),
]

urlpatterns += router.urls
