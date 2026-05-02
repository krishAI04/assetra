from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Count, Q, Sum
from django.http import FileResponse
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView
from django.views.generic.edit import CreateView, UpdateView
from django.views.generic.edit import FormView

from .forms import (
    ApprovalRejectForm,
    AssetForm,
    AuditLogFilterForm,
    BeneficiaryForm,
    ClientForm,
    DocumentForm,
    SignupForm,
    StyledAuthenticationForm,
    UserManagementForm,
)
from .models import Asset, AssetDistribution, AuditLog, Beneficiary, Client, Document, User
from .views import create_audit_log


class AssetraLoginView(LoginView):
    template_name = "auth/login.html"
    authentication_form = StyledAuthenticationForm
    redirect_authenticated_user = True


class AssetraSignupView(FormView):
    template_name = "auth/signup.html"
    form_class = SignupForm
    success_url = reverse_lazy("dashboard")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        messages.success(self.request, "Your Assetra workspace is ready.")
        return redirect(self.get_success_url())


class AssetraLogoutView(LogoutView):
    next_page = reverse_lazy("login")


class FrontendBaseMixin(LoginRequiredMixin):
    def get_firm(self):
        return self.request.user.firm

    def firm_clients(self):
        return Client.objects.filter(firm=self.get_firm())

    def firm_assets(self):
        return Asset.objects.filter(firm=self.get_firm())

    def firm_beneficiaries(self):
        return Beneficiary.objects.filter(firm=self.get_firm())

    def firm_documents(self):
        return Document.objects.filter(firm=self.get_firm())

    def firm_distributions(self):
        return AssetDistribution.objects.filter(firm=self.get_firm())

    def firm_audit_logs(self):
        return AuditLog.objects.filter(firm=self.get_firm())

    def render_closed_estate_forbidden(self, client):
        if client.status == Client.Status.CLOSED and self.request.user.role != User.Role.ADMIN:
            return HttpResponseForbidden("Closed estates are read-only unless reopened by an admin.")
        return None

    def common_context(self, **kwargs):
        return {
            "firm_name": self.request.user.firm.name if self.request.user.firm else "Assetra",
            **kwargs,
        }

    def user_can_access_document(self, document):
        """
        Allow access only to authenticated users from the same firm.
        Superusers can bypass firm restrictions.
        """
        user = self.request.user
        if user.is_superuser:
            return True
        return bool(user.firm_id and user.firm_id == document.firm_id)


class AdminRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        user = self.request.user
        return user.is_authenticated and (user.is_superuser or user.role == User.Role.ADMIN)


class ApproverRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        user = self.request.user
        return user.is_authenticated and (user.is_superuser or user.can_approve)


class HomeRedirectView(View):
    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return redirect("login")


class DashboardView(FrontendBaseMixin, TemplateView):
    template_name = "frontend/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        clients = self.firm_clients()
        assets = self.firm_assets()
        distributions = self.firm_distributions()
        context.update(
            self.common_context(
                stats={
                    "total_clients": clients.count(),
                    "total_assets": assets.count(),
                    "active_estates": clients.exclude(status=Client.Status.CLOSED).count(),
                    "pending_approvals": distributions.filter(
                        approval_status=AssetDistribution.ApprovalStatus.PENDING
                    ).count(),
                },
                recent_activity=self.firm_audit_logs().select_related("actor")[:8],
            )
        )
        return context


class ClientListView(FrontendBaseMixin, ListView):
    template_name = "frontend/clients/list.html"
    context_object_name = "clients"
    paginate_by = 10

    def get_queryset(self):
        queryset = (
            self.firm_clients()
            .prefetch_related("assignments__lawyer")
            .order_by("last_name", "first_name")
        )
        search = self.request.GET.get("search")
        status = self.request.GET.get("status")
        assigned_lawyer = self.request.GET.get("assigned_lawyer")
        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
            )
        if status:
            queryset = queryset.filter(status=status)
        if assigned_lawyer:
            queryset = queryset.filter(assignments__lawyer_id=assigned_lawyer)
        return queryset.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            self.common_context(
                lawyers=User.objects.filter(firm=self.get_firm(), role=User.Role.LAWYER).order_by(
                    "first_name", "last_name", "username"
                ),
                status_choices=Client.Status.choices,
                selected_status=self.request.GET.get("status", ""),
                selected_lawyer=self.request.GET.get("assigned_lawyer", ""),
                search_query=self.request.GET.get("search", ""),
            )
        )
        return context


class ClientCreateView(FrontendBaseMixin, CreateView):
    template_name = "frontend/clients/form.html"
    form_class = ClientForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        client = form.save(commit=False)
        client.firm = self.get_firm()
        client.full_clean()
        client.save()
        create_audit_log(self.request.user, "create", client, f"Created client {client.full_name}.")
        messages.success(self.request, "Client saved successfully.")
        return redirect("client-workspace", pk=client.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context(page_title="Add Client", form_mode="create"))
        return context


class ClientUpdateView(FrontendBaseMixin, UpdateView):
    template_name = "frontend/clients/form.html"
    form_class = ClientForm
    model = Client

    def get_queryset(self):
        return self.firm_clients()

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        blocked = self.render_closed_estate_forbidden(self.object)
        if blocked:
            return blocked
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        client = form.save(commit=False)
        client.full_clean()
        client.save()
        create_audit_log(self.request.user, "update", client, f"Updated client {client.full_name}.")
        messages.success(self.request, "Client updated successfully.")
        return redirect("client-workspace", pk=client.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context(page_title="Edit Client", form_mode="edit"))
        return context


class ClientDetailView(FrontendBaseMixin, DetailView):
    template_name = "frontend/clients/detail.html"
    model = Client
    context_object_name = "client"

    def get_queryset(self):
        return self.firm_clients().prefetch_related(
            "assignments__lawyer",
            "beneficiaries",
            "assets__documents",
            "documents",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        client = self.object
        assets = client.assets.all().prefetch_related("distributions__beneficiary")
        beneficiaries = client.beneficiaries.all()
        documents = client.documents.select_related("uploaded_by", "asset")
        distributions = (
            AssetDistribution.objects.filter(asset__client=client)
            .select_related("asset", "beneficiary", "approved_by")
            .order_by("asset__title", "beneficiary__last_name")
        )
        pending_approvals = distributions.filter(
            approval_status=AssetDistribution.ApprovalStatus.PENDING
        ).count()
        context.update(
            self.common_context(
                assignments=client.assignments.select_related("lawyer"),
                assets=assets[:6],
                beneficiaries=beneficiaries[:6],
                distributions=distributions[:8],
                documents=documents[:8],
                pending_approvals=pending_approvals,
                asset_count=assets.count(),
                beneficiary_count=beneficiaries.count(),
                document_count=documents.count(),
                activity=self.firm_audit_logs().filter(
                    Q(target_model="Client", target_id=client.pk)
                    | Q(metadata__client_id=client.pk)
                    | Q(description__icontains=client.full_name)
                )[:8],
            )
        )
        return context


class AssetCreateView(FrontendBaseMixin, CreateView):
    template_name = "frontend/assets/form.html"
    form_class = AssetForm

    def get_initial(self):
        initial = super().get_initial()
        client_id = self.request.GET.get("client")
        if client_id:
            initial["client"] = client_id
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        asset = form.save(commit=False)
        blocked = self.render_closed_estate_forbidden(asset.client)
        if blocked:
            return blocked
        asset.firm = self.get_firm()
        asset.full_clean()
        asset.save()
        create_audit_log(self.request.user, "create", asset, f"Created asset {asset.title}.")
        messages.success(self.request, "Asset saved successfully.")
        if "add_distribution" in self.request.POST:
            return redirect("approvals")
        return redirect("client-workspace", pk=asset.client_id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context(page_title="Add Asset", form_mode="create"))
        return context


class AssetUpdateView(FrontendBaseMixin, UpdateView):
    template_name = "frontend/assets/form.html"
    form_class = AssetForm
    model = Asset

    def get_queryset(self):
        return self.firm_assets()

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        blocked = self.render_closed_estate_forbidden(self.object.client)
        if blocked:
            return blocked
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        asset = form.save(commit=False)
        asset.full_clean()
        asset.save()
        create_audit_log(self.request.user, "update", asset, f"Updated asset {asset.title}.")
        messages.success(self.request, "Asset updated successfully.")
        return redirect("client-workspace", pk=asset.client_id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context(page_title="Edit Asset", form_mode="edit"))
        return context


class BeneficiaryCreateView(FrontendBaseMixin, CreateView):
    template_name = "frontend/beneficiaries/form.html"
    form_class = BeneficiaryForm

    def get_initial(self):
        initial = super().get_initial()
        client_id = self.request.GET.get("client")
        if client_id:
            initial["client"] = client_id
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        beneficiary = form.save(commit=False)
        blocked = self.render_closed_estate_forbidden(beneficiary.client)
        if blocked:
            return blocked
        beneficiary.firm = self.get_firm()
        beneficiary.full_clean()
        beneficiary.save()
        create_audit_log(
            self.request.user,
            "create",
            beneficiary,
            f"Created beneficiary {beneficiary}.",
        )
        messages.success(self.request, "Beneficiary saved successfully.")
        return redirect("client-workspace", pk=beneficiary.client_id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context(page_title="Add Beneficiary", form_mode="create"))
        return context


class BeneficiaryUpdateView(FrontendBaseMixin, UpdateView):
    template_name = "frontend/beneficiaries/form.html"
    form_class = BeneficiaryForm
    model = Beneficiary

    def get_queryset(self):
        return self.firm_beneficiaries()

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        blocked = self.render_closed_estate_forbidden(self.object.client)
        if blocked:
            return blocked
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        beneficiary = form.save(commit=False)
        beneficiary.full_clean()
        beneficiary.save()
        create_audit_log(self.request.user, "update", beneficiary, f"Updated beneficiary {beneficiary}.")
        messages.success(self.request, "Beneficiary updated successfully.")
        return redirect("client-workspace", pk=beneficiary.client_id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.common_context(page_title="Edit Beneficiary", form_mode="edit"))
        return context


class DocumentsView(FrontendBaseMixin, TemplateView):
    template_name = "frontend/documents/list.html"

    def get(self, request, *args, **kwargs):
        if not hasattr(self, "form"):
            self.form = DocumentForm(user=request.user)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        form = DocumentForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            document = form.save(commit=False)
            target_client = document.client or getattr(document.asset, "client", None)
            if target_client:
                blocked = self.render_closed_estate_forbidden(target_client)
                if blocked:
                    return blocked
            document.firm = self.get_firm()
            document.uploaded_by = request.user
            document.full_clean()
            document.save()
            create_audit_log(
                request.user,
                "upload",
                document,
                f"Uploaded document {document.title}.",
            )
            messages.success(request, "Document uploaded successfully.")
            return redirect("documents")
        self.form = form
        return self.render_to_response(self.get_context_data())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        documents = self.firm_documents().select_related("client", "asset", "uploaded_by")
        search = self.request.GET.get("search")
        client_id = self.request.GET.get("client")
        asset_id = self.request.GET.get("asset")
        if search:
            documents = documents.filter(title__icontains=search)
        if client_id:
            documents = documents.filter(client_id=client_id)
        if asset_id:
            documents = documents.filter(asset_id=asset_id)
        context.update(
            self.common_context(
                documents=documents[:50],
                form=getattr(self, "form", DocumentForm(user=self.request.user)),
                clients=self.firm_clients(),
                assets=self.firm_assets(),
                search_query=self.request.GET.get("search", ""),
                selected_client=client_id or "",
                selected_asset=asset_id or "",
            )
        )
        return context


class ProtectedDocumentView(FrontendBaseMixin, View):
    """
    Serve uploaded documents through a permission-checked Django view
    instead of exposing the media URL directly.
    """

    def get(self, request, pk):
        document = get_object_or_404(
            Document.objects.select_related("firm", "client", "asset"),
            pk=pk,
        )

        if not self.user_can_access_document(document):
            return HttpResponseForbidden("You do not have permission to view this document.")

        return FileResponse(document.file.open("rb"), as_attachment=False)


class ApprovalsView(FrontendBaseMixin, TemplateView):
    template_name = "frontend/approvals/list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        distributions = self.firm_distributions().select_related(
            "asset__client", "beneficiary", "approved_by"
        )
        status_value = self.request.GET.get("status")
        search = self.request.GET.get("search")
        selected_id = self.request.GET.get("selected")
        if status_value:
            distributions = distributions.filter(approval_status=status_value)
        if search:
            distributions = distributions.filter(
                Q(asset__title__icontains=search)
                | Q(asset__client__first_name__icontains=search)
                | Q(asset__client__last_name__icontains=search)
                | Q(beneficiary__first_name__icontains=search)
                | Q(beneficiary__last_name__icontains=search)
            )
        selected_distribution = None
        if selected_id:
            selected_distribution = distributions.filter(pk=selected_id).first()
        if not selected_distribution:
            selected_distribution = distributions.first()
        linked_documents = Document.objects.none()
        if selected_distribution:
            linked_documents = self.firm_documents().filter(
                Q(client=selected_distribution.asset.client) | Q(asset=selected_distribution.asset)
            )
        context.update(
            self.common_context(
                distributions=distributions[:50],
                selected_distribution=selected_distribution,
                linked_documents=linked_documents[:10],
                reject_form=ApprovalRejectForm(),
                status_choices=AssetDistribution.ApprovalStatus.choices,
                selected_status=status_value or "",
                search_query=search or "",
            )
        )
        return context


class ApproveDistributionView(ApproverRequiredMixin, FrontendBaseMixin, View):
    def post(self, request, pk):
        distribution = get_object_or_404(self.firm_distributions(), pk=pk)
        blocked = self.render_closed_estate_forbidden(distribution.asset.client)
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
            f"Approved distribution {distribution}.",
        )
        messages.success(request, "Distribution approved.")
        return redirect(f"{reverse('approvals')}?selected={distribution.pk}")


class RejectDistributionView(ApproverRequiredMixin, FrontendBaseMixin, View):
    def post(self, request, pk):
        distribution = get_object_or_404(self.firm_distributions(), pk=pk)
        blocked = self.render_closed_estate_forbidden(distribution.asset.client)
        if blocked:
            return blocked
        form = ApprovalRejectForm(request.POST)
        if form.is_valid():
            distribution.approval_status = AssetDistribution.ApprovalStatus.REJECTED
            distribution.rejection_reason = form.cleaned_data.get("rejection_reason", "")
            distribution.approved_by = None
            distribution.approved_at = None
            distribution.full_clean()
            distribution.save()
            create_audit_log(
                request.user,
                "reject",
                distribution,
                f"Rejected distribution {distribution}.",
            )
            messages.success(request, "Distribution rejected.")
        return redirect(f"{reverse('approvals')}?selected={distribution.pk}")


class UserManagementView(AdminRequiredMixin, FrontendBaseMixin, TemplateView):
    template_name = "frontend/users/list.html"

    def get(self, request, *args, **kwargs):
        if not hasattr(self, "form"):
            self.form = self.get_active_form()
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        edit_id = request.POST.get("edit_id")
        instance = None
        if edit_id:
            instance = get_object_or_404(User.objects.filter(firm=self.get_firm()), pk=edit_id)
        form = UserManagementForm(request.POST, user=request.user, instance=instance)
        if form.is_valid():
            user_obj = form.save(commit=False)
            user_obj.firm = self.get_firm()
            user_obj.full_clean()
            user_obj.save()
            action = "update" if instance else "create"
            create_audit_log(
                request.user,
                action,
                user_obj,
                f"{'Updated' if instance else 'Created'} user {user_obj.username}.",
            )
            messages.success(request, "User saved successfully.")
            return redirect("users")
        self.form = form
        return self.render_to_response(self.get_context_data())

    def get_active_form(self):
        edit_id = self.request.GET.get("edit")
        instance = None
        if edit_id:
            instance = get_object_or_404(User.objects.filter(firm=self.get_firm()), pk=edit_id)
        return UserManagementForm(user=self.request.user, instance=instance)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        users = User.objects.filter(firm=self.get_firm()).order_by("first_name", "last_name", "username")
        role_value = self.request.GET.get("role")
        status_value = self.request.GET.get("status")
        search = self.request.GET.get("search")
        if role_value:
            users = users.filter(role=role_value)
        if status_value == "active":
            users = users.filter(is_active=True)
        elif status_value == "inactive":
            users = users.filter(is_active=False)
        if search:
            users = users.filter(
                Q(username__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(email__icontains=search)
            )
        context.update(
            self.common_context(
                users=users[:50],
                form=getattr(self, "form", self.get_active_form()),
                role_choices=User.Role.choices,
                selected_role=role_value or "",
                selected_status=status_value or "",
                search_query=search or "",
                editing_user_id=self.request.GET.get("edit", ""),
            )
        )
        return context


class AuditLogPageView(FrontendBaseMixin, ListView):
    template_name = "frontend/audit_log/list.html"
    context_object_name = "logs"
    paginate_by = 20

    def get_queryset(self):
        queryset = self.firm_audit_logs().select_related("actor")
        form = AuditLogFilterForm(self.request.GET or None, user=self.request.user)
        self.filter_form = form
        if form.is_valid():
            action = form.cleaned_data.get("action")
            actor = form.cleaned_data.get("actor")
            if action:
                queryset = queryset.filter(action__icontains=action)
            if actor:
                queryset = queryset.filter(actor=actor)
        search = self.request.GET.get("search")
        if search:
            queryset = queryset.filter(
                Q(description__icontains=search)
                | Q(target_model__icontains=search)
                | Q(action__icontains=search)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            self.common_context(
                filter_form=getattr(self, "filter_form", AuditLogFilterForm(user=self.request.user)),
                search_query=self.request.GET.get("search", ""),
            )
        )
        return context
