from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.http import FileResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic.edit import FormView

from .forms import (
    ApprovalRejectForm,
    AssetForm,
    AssetDistributionForm,
    AuditLogFilterForm,
    BeneficiaryForm,
    ClientForm,
    CommentForm,
    DocumentForm,
    DocumentVersionForm,
    SignupForm,
    StyledAuthenticationForm,
    TaskForm,
    UserManagementForm,
)
from .models import (
    AIDocumentAnalysis,
    Asset,
    AssetDistribution,
    AuditLog,
    Beneficiary,
    Client,
    Comment,
    Document,
    DocumentVersion,
    Notification,
    Task,
    User,
)
from .ai_tools import analyze_document
from .views import create_audit_log


def create_notification(firm, message, notification_type=Notification.Type.INFO, target_url="", user=None):
    """Create one simple in-app notification for a firm or a specific user."""
    return Notification.objects.create(
        firm=firm,
        user=user,
        message=message,
        notification_type=notification_type,
        target_url=target_url,
    )


class AssetraLoginView(LoginView):
    """Show the login page and let Django handle authentication."""

    template_name = "auth/login.html"
    authentication_form = StyledAuthenticationForm
    redirect_authenticated_user = True


class AssetraSignupView(FormView):
    """Create a new firm admin account and log the user in immediately."""

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
    """Log the current user out and send them back to login."""

    next_page = reverse_lazy("login")


class FrontendBaseMixin(LoginRequiredMixin):
    """
    Shared helpers used by most frontend pages.

    This mixin keeps repeated logic in one place:
    - getting the logged-in user's firm
    - filtering data by firm
    - checking whether an estate is closed
    - building common template context
    """

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

    def firm_notifications(self):
        return Notification.objects.filter(firm=self.get_firm()).filter(
            Q(user=self.request.user) | Q(user__isnull=True)
        )

    def firm_tasks(self):
        return Task.objects.filter(firm=self.get_firm())

    def firm_comments(self):
        return Comment.objects.filter(firm=self.get_firm())

    def firm_document_versions(self):
        return DocumentVersion.objects.filter(firm=self.get_firm())

    def firm_ai_document_analyses(self):
        return AIDocumentAnalysis.objects.filter(firm=self.get_firm())

    def common_context(self, **extra_context):
        """Add values that almost every page needs."""
        notifications = self.firm_notifications()
        return {
            "firm_name": self.request.user.firm.name if self.request.user.firm else "Assetra",
            "topbar_notifications": notifications[:5],
            "topbar_unread_notifications": notifications.filter(is_read=False).count(),
            "global_search_query": self.request.GET.get("q", ""),
            "my_open_task_count": self.firm_tasks()
            .filter(assigned_to=self.request.user)
            .exclude(status=Task.Status.COMPLETED)
            .count(),
            **extra_context,
        }

    def render_page(self, request, template_name, context=None, status=200):
        """Simple wrapper around Django's render for consistent pages."""
        return render(request, template_name, self.common_context(**(context or {})), status=status)

    def paginate_queryset(self, queryset, per_page):
        """Basic pagination helper for list pages."""
        paginator = Paginator(queryset, per_page)
        page_number = self.request.GET.get("page")
        return paginator.get_page(page_number)

    def render_closed_estate_forbidden(self, client):
        """
        Prevent non-admin users from editing data when the estate is closed.
        """
        if client.status == Client.Status.CLOSED and self.request.user.role != User.Role.ADMIN:
            return HttpResponseForbidden("Closed estates are read-only unless reopened by an admin.")
        return None

    def user_can_access_document(self, document):
        """
        Allow access only to users from the same firm.
        Superusers can bypass this check.
        """
        user = self.request.user
        if user.is_superuser:
            return True
        return bool(user.firm_id and user.firm_id == document.firm_id)


class AdminRequiredMixin(UserPassesTestMixin):
    """Allow only admins or superusers to use the page."""

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and (user.is_superuser or user.role == User.Role.ADMIN)


class ApproverRequiredMixin(UserPassesTestMixin):
    """Allow only approvers or superusers to use the page."""

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and (user.is_superuser or user.can_approve)


class HomeRedirectView(View):
    """Show the public landing page, or send logged-in users to the dashboard."""

    template_name = "frontend/landing.html"

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return render(request, self.template_name)


class DashboardView(FrontendBaseMixin, View):
    """Render the dashboard with counts and recent firm activity."""

    template_name = "frontend/dashboard.html"

    def get(self, request):
        clients = self.firm_clients()
        assets = self.firm_assets()
        distributions = self.firm_distributions()
        asset_rows = assets.values("category").annotate(total=Count("id")).order_by("category")
        approval_rows = distributions.values("approval_status").annotate(total=Count("id"))
        monthly_rows = (
            self.firm_audit_logs()
            .annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(total=Count("id"))
            .order_by("month")
        )

        context = {
            "stats": {
                "total_clients": clients.count(),
                "total_assets": assets.count(),
                "active_estates": clients.exclude(status=Client.Status.CLOSED).count(),
                "pending_approvals": distributions.filter(
                    approval_status=AssetDistribution.ApprovalStatus.PENDING
                ).count(),
            },
            "chart_data": {
                "assets_by_type": {
                    "labels": [
                        dict(Asset.Category.choices).get(row["category"], row["category"])
                        for row in asset_rows
                    ],
                    "values": [row["total"] for row in asset_rows],
                },
                "approval_status": {
                    "labels": [
                        dict(AssetDistribution.ApprovalStatus.choices).get(
                            row["approval_status"],
                            row["approval_status"],
                        )
                        for row in approval_rows
                    ],
                    "values": [row["total"] for row in approval_rows],
                },
                "monthly_activity": {
                    "labels": [row["month"].strftime("%b %Y") for row in monthly_rows if row["month"]],
                    "values": [row["total"] for row in monthly_rows if row["month"]],
                },
            },
            "recent_activity": self.firm_audit_logs().select_related("actor")[:8],
            "my_tasks": self.firm_tasks()
            .select_related("client", "assigned_to")
            .filter(assigned_to=request.user)
            .exclude(status=Task.Status.COMPLETED)[:6],
        }
        return self.render_page(request, self.template_name, context)


class GlobalSearchView(FrontendBaseMixin, View):
    """Search important firm records from one topbar search box."""

    template_name = "frontend/search/results.html"

    def get(self, request):
        query = request.GET.get("q", "").strip()
        results = {
            "clients": Client.objects.none(),
            "assets": Asset.objects.none(),
            "beneficiaries": Beneficiary.objects.none(),
            "documents": Document.objects.none(),
        }

        if query:
            results["clients"] = self.firm_clients().filter(
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(email__icontains=query)
                | Q(phone__icontains=query)
            )[:8]
            results["assets"] = self.firm_assets().select_related("client").filter(
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(client__first_name__icontains=query)
                | Q(client__last_name__icontains=query)
            )[:8]
            results["beneficiaries"] = self.firm_beneficiaries().select_related("client").filter(
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(email__icontains=query)
                | Q(relationship_to_client__icontains=query)
            )[:8]
            results["documents"] = self.firm_documents().select_related("client", "asset").filter(
                Q(title__icontains=query)
                | Q(client__first_name__icontains=query)
                | Q(client__last_name__icontains=query)
                | Q(asset__title__icontains=query)
            )[:8]

        context = {
            "query": query,
            "results": results,
            "total_results": sum(len(items) for items in results.values()),
        }
        return self.render_page(request, self.template_name, context)


class ClientListView(FrontendBaseMixin, View):
    """Show all clients for the current firm with filters and pagination."""

    template_name = "frontend/clients/list.html"

    def get_queryset(self):
        queryset = self.firm_clients().prefetch_related("assignments__lawyer").order_by(
            "last_name", "first_name"
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

    def get(self, request):
        queryset = self.get_queryset()
        page_obj = self.paginate_queryset(queryset, per_page=10)
        context = {
            "clients": page_obj,
            "page_obj": page_obj,
            "lawyers": User.objects.filter(firm=self.get_firm(), role=User.Role.LAWYER).order_by(
                "first_name", "last_name", "username"
            ),
            "status_choices": Client.Status.choices,
            "selected_status": request.GET.get("status", ""),
            "selected_lawyer": request.GET.get("assigned_lawyer", ""),
            "search_query": request.GET.get("search", ""),
        }
        return self.render_page(request, self.template_name, context)


class ClientCreateView(FrontendBaseMixin, View):
    """Show the add client form and save a new client."""

    template_name = "frontend/clients/form.html"
    form_class = ClientForm

    def get_form(self, data=None):
        return self.form_class(data=data, user=self.request.user)

    def get(self, request):
        context = {
            "form": self.get_form(),
            "page_title": "Add Client",
            "form_mode": "create",
        }
        return self.render_page(request, self.template_name, context)

    def post(self, request):
        form = self.get_form(data=request.POST)
        if form.is_valid():
            client = form.save(commit=False)
            client.firm = self.get_firm()
            client.full_clean()
            client.save()
            create_audit_log(request.user, "create", client, f"Created client {client.full_name}.")
            create_notification(
                self.get_firm(),
                f"New client added: {client.full_name}",
                Notification.Type.CLIENT,
                reverse("client-workspace", kwargs={"pk": client.pk}),
            )
            messages.success(request, "Client saved successfully.")
            return redirect("client-workspace", pk=client.pk)

        context = {"form": form, "page_title": "Add Client", "form_mode": "create"}
        return self.render_page(request, self.template_name, context)


class ClientUpdateView(FrontendBaseMixin, View):
    """Show the edit client form and update the selected client."""

    template_name = "frontend/clients/form.html"
    form_class = ClientForm

    def get_object(self, pk):
        return get_object_or_404(self.firm_clients(), pk=pk)

    def get_form(self, client, data=None):
        return self.form_class(data=data, instance=client, user=self.request.user)

    def get(self, request, pk):
        client = self.get_object(pk)
        blocked = self.render_closed_estate_forbidden(client)
        if blocked:
            return blocked

        context = {
            "form": self.get_form(client),
            "object": client,
            "client": client,
            "page_title": "Edit Client",
            "form_mode": "edit",
        }
        return self.render_page(request, self.template_name, context)

    def post(self, request, pk):
        client = self.get_object(pk)
        blocked = self.render_closed_estate_forbidden(client)
        if blocked:
            return blocked

        form = self.get_form(client, data=request.POST)
        if form.is_valid():
            client = form.save(commit=False)
            client.full_clean()
            client.save()
            create_audit_log(request.user, "update", client, f"Updated client {client.full_name}.")
            messages.success(request, "Client updated successfully.")
            return redirect("client-workspace", pk=client.pk)

        context = {
            "form": form,
            "object": client,
            "client": client,
            "page_title": "Edit Client",
            "form_mode": "edit",
        }
        return self.render_page(request, self.template_name, context)


class ClientDetailView(FrontendBaseMixin, View):
    """Show the full workspace page for one client."""

    template_name = "frontend/clients/detail.html"

    def get_client(self, pk):
        return get_object_or_404(
            self.firm_clients().prefetch_related(
                "assignments__lawyer",
                "beneficiaries",
                "assets__documents",
                "documents",
            ),
            pk=pk,
        )

    def get(self, request, pk):
        client = self.get_client(pk)
        assets = client.assets.all().prefetch_related("distributions__beneficiary")
        beneficiaries = client.beneficiaries.all()
        documents = client.documents.select_related("uploaded_by", "asset")
        distributions = (
            AssetDistribution.objects.filter(asset__client=client)
            .select_related("asset", "beneficiary", "approved_by")
            .order_by("asset__title", "beneficiary__last_name")
        )

        context = {
            "client": client,
            "object": client,
            "assignments": client.assignments.select_related("lawyer"),
            "assets": assets[:6],
            "beneficiaries": beneficiaries[:6],
            "distributions": distributions[:8],
            "documents": documents[:8],
            "pending_approvals": distributions.filter(
                approval_status=AssetDistribution.ApprovalStatus.PENDING
            ).count(),
            "asset_count": assets.count(),
            "beneficiary_count": beneficiaries.count(),
            "document_count": documents.count(),
            "tasks": self.firm_tasks().filter(client=client).select_related("assigned_to")[:8],
            "task_form": TaskForm(user=request.user, initial={"client": client.pk}),
            "comments": self.firm_comments().filter(client=client).select_related("author")[:8],
            "comment_form": CommentForm(user=request.user),
            "activity": self.firm_audit_logs().filter(
                Q(target_model="Client", target_id=client.pk)
                | Q(metadata__client_id=client.pk)
                | Q(description__icontains=client.full_name)
            )[:8],
        }
        return self.render_page(request, self.template_name, context)


class TaskListView(FrontendBaseMixin, View):
    """Show firm tasks and let users create simple work assignments."""

    template_name = "frontend/tasks/list.html"

    def get_filtered_tasks(self):
        tasks = self.firm_tasks().select_related("client", "assigned_to", "created_by")
        status_value = self.request.GET.get("status")
        assigned_value = self.request.GET.get("assigned")
        search = self.request.GET.get("search")

        if status_value == "overdue":
            tasks = tasks.exclude(status=Task.Status.COMPLETED).filter(due_date__lt=timezone.localdate())
        elif status_value:
            tasks = tasks.filter(status=status_value)
        if assigned_value == "me":
            tasks = tasks.filter(assigned_to=self.request.user)
        elif assigned_value:
            tasks = tasks.filter(assigned_to_id=assigned_value)
        if search:
            tasks = tasks.filter(Q(title__icontains=search) | Q(description__icontains=search))

        return tasks, status_value, assigned_value, search

    def get_context(self, form):
        tasks, status_value, assigned_value, search = self.get_filtered_tasks()
        return {
            "tasks": tasks[:80],
            "form": form,
            "status_choices": Task.Status.choices,
            "priority_choices": Task.Priority.choices,
            "users": User.objects.filter(firm=self.get_firm()).order_by("first_name", "last_name", "username"),
            "selected_status": status_value or "",
            "selected_assigned": assigned_value or "",
            "search_query": search or "",
        }

    def get(self, request):
        return self.render_page(request, self.template_name, self.get_context(TaskForm(user=request.user)))

    def post(self, request):
        form = TaskForm(request.POST, user=request.user)
        if form.is_valid():
            task = form.save(commit=False)
            task.firm = self.get_firm()
            task.created_by = request.user
            task.full_clean()
            task.save()
            create_audit_log(request.user, "create", task, f"Created task {task.title}.")
            create_notification(
                self.get_firm(),
                f"Task assigned: {task.title}",
                Notification.Type.INFO,
                reverse("tasks"),
                user=task.assigned_to,
            )
            messages.success(request, "Task created successfully.")
            return redirect("tasks")

        messages.error(request, "Task could not be saved. Please check the form.")
        return self.render_page(request, self.template_name, self.get_context(form))


class TaskUpdateView(FrontendBaseMixin, View):
    """Edit an existing task."""

    template_name = "frontend/tasks/form.html"

    def get_task(self, pk):
        return get_object_or_404(self.firm_tasks(), pk=pk)

    def get(self, request, pk):
        task = self.get_task(pk)
        form = TaskForm(user=request.user, instance=task)
        return self.render_page(request, self.template_name, {"form": form, "task": task})

    def post(self, request, pk):
        task = self.get_task(pk)
        form = TaskForm(request.POST, user=request.user, instance=task)
        if form.is_valid():
            task = form.save(commit=False)
            task.full_clean()
            task.save()
            create_audit_log(request.user, "update", task, f"Updated task {task.title}.")
            messages.success(request, "Task updated successfully.")
            return redirect("tasks")

        return self.render_page(request, self.template_name, {"form": form, "task": task})


class TaskStatusView(FrontendBaseMixin, View):
    """Quickly change task status from task lists."""

    def post(self, request, pk):
        task = get_object_or_404(self.firm_tasks(), pk=pk)
        status_value = request.POST.get("status")
        if status_value in Task.Status.values:
            task.status = status_value
            task.full_clean()
            task.save()
            create_audit_log(request.user, "update", task, f"Changed task {task.title} to {task.get_status_display()}.")
            messages.success(request, "Task status updated.")
        return redirect(request.POST.get("next") or "tasks")


class ClientCommentCreateView(FrontendBaseMixin, View):
    """Add an internal comment to a client workspace."""

    def post(self, request, pk):
        client = get_object_or_404(self.firm_clients(), pk=pk)
        form = CommentForm(request.POST, user=request.user)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.firm = self.get_firm()
            comment.author = request.user
            comment.client = client
            comment.full_clean()
            comment.save()
            create_audit_log(request.user, "comment", comment, f"Commented on client {client.full_name}.")
            create_notification(
                self.get_firm(),
                f"New comment on {client.full_name}",
                Notification.Type.CLIENT,
                reverse("client-workspace", kwargs={"pk": client.pk}),
            )
            messages.success(request, "Comment added.")
        else:
            messages.error(request, "Comment could not be added.")
        return redirect(f"{reverse('client-workspace', kwargs={'pk': client.pk})}#comments")


class DocumentCommentCreateView(FrontendBaseMixin, View):
    """Add an internal comment to a document."""

    def post(self, request, pk):
        document = get_object_or_404(self.firm_documents(), pk=pk)
        form = CommentForm(request.POST, user=request.user)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.firm = self.get_firm()
            comment.author = request.user
            comment.document = document
            comment.client = document.client
            comment.asset = document.asset
            comment.full_clean()
            comment.save()
            create_audit_log(request.user, "comment", comment, f"Commented on document {document.title}.")
            create_notification(
                self.get_firm(),
                f"New comment on document: {document.title}",
                Notification.Type.DOCUMENT,
                reverse("document-preview", kwargs={"pk": document.pk}),
            )
            messages.success(request, "Comment added.")
        else:
            messages.error(request, "Comment could not be added.")
        return redirect(f"{reverse('document-preview', kwargs={'pk': document.pk})}#document-comments")


class AssetCreateView(FrontendBaseMixin, View):
    """Show the add asset form and save a new asset."""

    template_name = "frontend/assets/form.html"
    form_class = AssetForm

    def get_initial(self):
        initial = {}
        client_id = self.request.GET.get("client")
        if client_id:
            initial["client"] = client_id
        return initial

    def get_form(self, data=None):
        return self.form_class(data=data, user=self.request.user, initial=self.get_initial())

    def get(self, request):
        context = {
            "form": self.get_form(),
            "page_title": "Add Asset",
            "form_mode": "create",
        }
        return self.render_page(request, self.template_name, context)

    def post(self, request):
        form = self.form_class(request.POST, user=request.user)
        if form.is_valid():
            asset = form.save(commit=False)
            blocked = self.render_closed_estate_forbidden(asset.client)
            if blocked:
                return blocked

            asset.firm = self.get_firm()
            asset.full_clean()
            asset.save()
            create_audit_log(request.user, "create", asset, f"Created asset {asset.title}.")
            messages.success(request, "Asset saved successfully.")
            if "add_distribution" in request.POST:
                return redirect(f"{reverse('distribution-create')}?asset={asset.pk}&client={asset.client_id}")
            return redirect("client-workspace", pk=asset.client_id)

        context = {"form": form, "page_title": "Add Asset", "form_mode": "create"}
        return self.render_page(request, self.template_name, context)


class AssetUpdateView(FrontendBaseMixin, View):
    """Show the edit asset form and update an existing asset."""

    template_name = "frontend/assets/form.html"
    form_class = AssetForm

    def get_object(self, pk):
        return get_object_or_404(self.firm_assets(), pk=pk)

    def get_form(self, asset, data=None):
        return self.form_class(data=data, instance=asset, user=self.request.user)

    def get(self, request, pk):
        asset = self.get_object(pk)
        blocked = self.render_closed_estate_forbidden(asset.client)
        if blocked:
            return blocked

        context = {
            "form": self.get_form(asset),
            "object": asset,
            "page_title": "Edit Asset",
            "form_mode": "edit",
        }
        return self.render_page(request, self.template_name, context)

    def post(self, request, pk):
        asset = self.get_object(pk)
        blocked = self.render_closed_estate_forbidden(asset.client)
        if blocked:
            return blocked

        form = self.get_form(asset, data=request.POST)
        if form.is_valid():
            asset = form.save(commit=False)
            asset.full_clean()
            asset.save()
            create_audit_log(request.user, "update", asset, f"Updated asset {asset.title}.")
            messages.success(request, "Asset updated successfully.")
            return redirect("client-workspace", pk=asset.client_id)

        context = {
            "form": form,
            "object": asset,
            "page_title": "Edit Asset",
            "form_mode": "edit",
        }
        return self.render_page(request, self.template_name, context)


class BeneficiaryCreateView(FrontendBaseMixin, View):
    """Show the add beneficiary form and save a new beneficiary."""

    template_name = "frontend/beneficiaries/form.html"
    form_class = BeneficiaryForm

    def get_initial(self):
        initial = {}
        client_id = self.request.GET.get("client")
        if client_id:
            initial["client"] = client_id
        return initial

    def get_form(self, data=None):
        return self.form_class(data=data, user=self.request.user, initial=self.get_initial())

    def get(self, request):
        context = {
            "form": self.get_form(),
            "page_title": "Add Beneficiary",
            "form_mode": "create",
        }
        return self.render_page(request, self.template_name, context)

    def post(self, request):
        form = self.form_class(request.POST, user=request.user)
        if form.is_valid():
            beneficiary = form.save(commit=False)
            blocked = self.render_closed_estate_forbidden(beneficiary.client)
            if blocked:
                return blocked

            beneficiary.firm = self.get_firm()
            beneficiary.full_clean()
            beneficiary.save()
            create_audit_log(request.user, "create", beneficiary, f"Created beneficiary {beneficiary}.")
            messages.success(request, "Beneficiary saved successfully.")
            return redirect("client-workspace", pk=beneficiary.client_id)

        context = {"form": form, "page_title": "Add Beneficiary", "form_mode": "create"}
        return self.render_page(request, self.template_name, context)


class BeneficiaryUpdateView(FrontendBaseMixin, View):
    """Show the edit beneficiary form and update an existing beneficiary."""

    template_name = "frontend/beneficiaries/form.html"
    form_class = BeneficiaryForm

    def get_object(self, pk):
        return get_object_or_404(self.firm_beneficiaries(), pk=pk)

    def get_form(self, beneficiary, data=None):
        return self.form_class(data=data, instance=beneficiary, user=self.request.user)

    def get(self, request, pk):
        beneficiary = self.get_object(pk)
        blocked = self.render_closed_estate_forbidden(beneficiary.client)
        if blocked:
            return blocked

        context = {
            "form": self.get_form(beneficiary),
            "object": beneficiary,
            "page_title": "Edit Beneficiary",
            "form_mode": "edit",
        }
        return self.render_page(request, self.template_name, context)

    def post(self, request, pk):
        beneficiary = self.get_object(pk)
        blocked = self.render_closed_estate_forbidden(beneficiary.client)
        if blocked:
            return blocked

        form = self.get_form(beneficiary, data=request.POST)
        if form.is_valid():
            beneficiary = form.save(commit=False)
            beneficiary.full_clean()
            beneficiary.save()
            create_audit_log(
                request.user,
                "update",
                beneficiary,
                f"Updated beneficiary {beneficiary}.",
            )
            messages.success(request, "Beneficiary updated successfully.")
            return redirect("client-workspace", pk=beneficiary.client_id)

        context = {
            "form": form,
            "object": beneficiary,
            "page_title": "Edit Beneficiary",
            "form_mode": "edit",
        }
        return self.render_page(request, self.template_name, context)


class DistributionCreateView(FrontendBaseMixin, View):
    """
    Show a simple form for linking an asset to a beneficiary.

    This is the page used to create a new distribution record.
    """

    template_name = "frontend/distributions/form.html"
    form_class = AssetDistributionForm

    def get_initial(self):
        initial = {}

        # Pre-fill values when the user comes from another page.
        client_id = self.request.GET.get("client")
        asset_id = self.request.GET.get("asset")

        if client_id:
            initial["client"] = client_id
        if asset_id:
            initial["asset"] = asset_id

        return initial

    def get_form(self, data=None):
        return self.form_class(data=data, user=self.request.user, initial=self.get_initial())

    def get(self, request):
        context = {
            "form": self.get_form(),
            "page_title": "Add Distribution",
            "form_mode": "create",
        }
        return self.render_page(request, self.template_name, context)

    def post(self, request):
        form = self.form_class(request.POST, user=request.user, initial=self.get_initial())
        if form.is_valid():
            distribution = form.save(commit=False)

            # Keep the new record inside the current user's firm.
            distribution.firm = self.get_firm()

            # Closed client estates should not accept new distributions.
            blocked = self.render_closed_estate_forbidden(distribution.asset.client)
            if blocked:
                return blocked

            distribution.full_clean()
            distribution.save()

            create_audit_log(
                request.user,
                "create",
                distribution,
                f"Created distribution {distribution}.",
            )
            create_notification(
                self.get_firm(),
                f"Asset awaiting approval: {distribution.asset.title}",
                Notification.Type.APPROVAL,
                f"{reverse('approvals')}?selected={distribution.pk}",
            )
            messages.success(request, "Distribution created successfully.")
            return redirect(f"{reverse('approvals')}?selected={distribution.pk}")

        context = {
            "form": form,
            "page_title": "Add Distribution",
            "form_mode": "create",
        }
        return self.render_page(request, self.template_name, context)


class DocumentsView(FrontendBaseMixin, View):
    """List documents and handle document uploads on the same page."""

    template_name = "frontend/documents/list.html"

    def get_filtered_documents(self):
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

        return documents, search, client_id, asset_id

    def get_context(self, form):
        documents, search, client_id, asset_id = self.get_filtered_documents()
        return {
            "documents": documents[:50],
            "form": form,
            "clients": self.firm_clients(),
            "assets": self.firm_assets(),
            "asset_client_map": form.asset_client_map,
            "search_query": search or "",
            "selected_client": client_id or "",
            "selected_asset": asset_id or "",
        }

    def get(self, request):
        form = DocumentForm(user=request.user)
        return self.render_page(request, self.template_name, self.get_context(form))

    def post(self, request):
        form = DocumentForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            document = form.save(commit=False)
            if document.asset and not document.client:
                document.client = document.asset.client
            target_client = document.client or getattr(document.asset, "client", None)
            if target_client:
                blocked = self.render_closed_estate_forbidden(target_client)
                if blocked:
                    return blocked

            document.firm = self.get_firm()
            document.uploaded_by = request.user
            document.full_clean()
            document.save()
            create_audit_log(request.user, "upload", document, f"Uploaded document {document.title}.")
            create_notification(
                self.get_firm(),
                f"Document uploaded: {document.title}",
                Notification.Type.DOCUMENT,
                reverse("document-preview", kwargs={"pk": document.pk}),
            )
            messages.success(request, "Document uploaded successfully.")
            return redirect("documents")

        messages.error(request, "Document upload failed. Please check the highlighted fields.")
        return self.render_page(request, self.template_name, self.get_context(form))


class ProtectedDocumentView(FrontendBaseMixin, View):
    """
    Open uploaded files only after checking login and firm access.
    This keeps sensitive files behind Django permission checks.
    """

    def get(self, request, pk):
        document = get_object_or_404(
            Document.objects.select_related("firm", "client", "asset"),
            pk=pk,
        )
        if not self.user_can_access_document(document):
            return HttpResponseForbidden("You do not have permission to view this document.")
        latest_version = document.versions.order_by("-version_number").first()
        file_to_open = latest_version.file if latest_version else document.file
        response = FileResponse(file_to_open.open("rb"), as_attachment=False)
        response["X-Frame-Options"] = "SAMEORIGIN"
        return response


class DocumentPreviewView(FrontendBaseMixin, View):
    """Show document details beside a protected browser PDF preview."""

    template_name = "frontend/documents/preview.html"

    def get(self, request, pk):
        document = get_object_or_404(
            Document.objects.select_related("firm", "client", "asset", "uploaded_by"),
            pk=pk,
        )
        if not self.user_can_access_document(document):
            return HttpResponseForbidden("You do not have permission to view this document.")

        context = {
            "document": document,
            "versions": document.versions.select_related("uploaded_by"),
            "version_form": DocumentVersionForm(user=request.user),
            "comments": self.firm_comments().filter(document=document).select_related("author")[:10],
            "comment_form": CommentForm(user=request.user),
            "latest_ai_analysis": document.ai_analyses.select_related("created_by").first(),
            "ai_mode": getattr(settings, "ASSETRA_AI_MODE", "mock"),
        }
        return self.render_page(request, self.template_name, context)


class DocumentAIAnalyzeView(FrontendBaseMixin, View):
    """Create an AI document analysis in mock mode or live Gemini mode."""

    def live_ai_allowed(self):
        live_mode_enabled = getattr(settings, "ASSETRA_AI_MODE", "mock") == "live"
        if not live_mode_enabled:
            return False

        today = timezone.localdate()
        live_calls_today = self.firm_ai_document_analyses().filter(
            mode=AIDocumentAnalysis.Mode.LIVE,
            created_at__date=today,
        ).count()
        return live_calls_today < getattr(settings, "ASSETRA_AI_DAILY_LIMIT", 10)

    def post(self, request, pk):
        document = get_object_or_404(self.firm_documents(), pk=pk)
        if not self.user_can_access_document(document):
            return HttpResponseForbidden("You do not have permission to analyze this document.")

        use_live_ai = self.live_ai_allowed()
        mode, data = analyze_document(document, use_live_ai=use_live_ai)

        analysis = AIDocumentAnalysis(
            firm=self.get_firm(),
            document=document,
            created_by=request.user,
            mode=mode,
            summary=data.get("summary", ""),
            important_parties=data.get("important_parties", []),
            important_dates=data.get("important_dates", []),
            asset_details=data.get("asset_details", {}),
            risk_points=data.get("risk_points", []),
            suggested_next_action=data.get("suggested_next_action", ""),
            raw_response=data.get("raw_response", {}),
        )
        analysis.full_clean()
        analysis.save()

        create_audit_log(request.user, "ai_analyze", analysis, f"Analyzed document {document.title}.")
        create_notification(
            self.get_firm(),
            f"AI analysis ready: {document.title}",
            Notification.Type.DOCUMENT,
            reverse("document-preview", kwargs={"pk": document.pk}),
        )
        if mode == AIDocumentAnalysis.Mode.LIVE:
            messages.success(request, "Live Gemini analysis generated.")
        else:
            messages.success(request, "Demo AI analysis generated.")
        return redirect(f"{reverse('document-preview', kwargs={'pk': document.pk})}#ai-analysis")


class DocumentVersionCreateView(FrontendBaseMixin, View):
    """Upload a new file version for an existing document."""

    def post(self, request, pk):
        document = get_object_or_404(self.firm_documents(), pk=pk)
        form = DocumentVersionForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            version = form.save(commit=False)
            version.firm = self.get_firm()
            version.document = document
            version.uploaded_by = request.user
            latest = document.versions.order_by("-version_number").first()
            version.version_number = 1 if latest is None else latest.version_number + 1
            version.full_clean()
            version.save()
            create_audit_log(request.user, "upload", version, f"Uploaded {version}.")
            create_notification(
                self.get_firm(),
                f"New document version: {document.title} v{version.version_number}",
                Notification.Type.DOCUMENT,
                reverse("document-preview", kwargs={"pk": document.pk}),
            )
            messages.success(request, "New document version uploaded.")
        else:
            messages.error(request, "Version upload failed. Please check the form.")
        return redirect(f"{reverse('document-preview', kwargs={'pk': document.pk})}#versions")


class ApprovalsView(FrontendBaseMixin, View):
    """Show asset distributions and let approvers review them."""

    template_name = "frontend/approvals/list.html"

    def get_distributions(self):
        distributions = self.firm_distributions().select_related(
            "asset__client", "beneficiary", "approved_by"
        )
        status_value = self.request.GET.get("status")
        search = self.request.GET.get("search")

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

        return distributions, status_value, search

    def get(self, request):
        distributions, status_value, search = self.get_distributions()
        selected_id = request.GET.get("selected")

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

        context = {
            "distributions": distributions[:50],
            "selected_distribution": selected_distribution,
            "linked_documents": linked_documents[:10],
            "reject_form": ApprovalRejectForm(),
            "status_choices": AssetDistribution.ApprovalStatus.choices,
            "selected_status": status_value or "",
            "search_query": search or "",
        }
        return self.render_page(request, self.template_name, context)


class ApproveDistributionView(ApproverRequiredMixin, FrontendBaseMixin, View):
    """Approve a pending asset distribution."""

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
        create_notification(
            self.get_firm(),
            f"Distribution approved: {distribution.asset.title}",
            Notification.Type.APPROVAL,
            f"{reverse('approvals')}?selected={distribution.pk}",
        )
        messages.success(request, "Distribution approved.")
        return redirect(f"{reverse('approvals')}?selected={distribution.pk}")


class RejectDistributionView(ApproverRequiredMixin, FrontendBaseMixin, View):
    """Reject a pending asset distribution and save the rejection reason."""

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
            create_notification(
                self.get_firm(),
                f"Distribution rejected: {distribution.asset.title}",
                Notification.Type.APPROVAL,
                f"{reverse('approvals')}?selected={distribution.pk}",
            )
            messages.success(request, "Distribution rejected.")

        return redirect(f"{reverse('approvals')}?selected={distribution.pk}")


class UserManagementView(AdminRequiredMixin, FrontendBaseMixin, View):
    """List firm users and handle add/edit actions from the same page."""

    template_name = "frontend/users/list.html"

    def get_edit_instance(self):
        edit_id = self.request.GET.get("edit") or self.request.POST.get("edit_id")
        if not edit_id:
            return None
        return get_object_or_404(User.objects.filter(firm=self.get_firm()), pk=edit_id)

    def get_active_form(self, instance=None, data=None):
        return UserManagementForm(data=data, user=self.request.user, instance=instance)

    def get_filtered_users(self):
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

        return users, role_value, status_value, search

    def get_context(self, form):
        users, role_value, status_value, search = self.get_filtered_users()
        return {
            "users": users[:50],
            "form": form,
            "role_choices": User.Role.choices,
            "selected_role": role_value or "",
            "selected_status": status_value or "",
            "search_query": search or "",
            "editing_user_id": self.request.GET.get("edit", ""),
        }

    def get(self, request):
        form = self.get_active_form(instance=self.get_edit_instance())
        return self.render_page(request, self.template_name, self.get_context(form))

    def post(self, request):
        instance = self.get_edit_instance()
        form = self.get_active_form(instance=instance, data=request.POST)

        if form.is_valid():
            user_obj = form.save(commit=False)
            user_obj.firm = self.get_firm()
            user_obj.full_clean()
            user_obj.save()

            action = "update" if instance else "create"
            description = f"{'Updated' if instance else 'Created'} user {user_obj.username}."
            create_audit_log(request.user, action, user_obj, description)
            if not instance:
                create_notification(
                    self.get_firm(),
                    f"New user added: {user_obj.username}",
                    Notification.Type.USER,
                    reverse("users"),
                )
            messages.success(request, "User saved successfully.")
            return redirect("users")

        return self.render_page(request, self.template_name, self.get_context(form))


class AuditLogPageView(FrontendBaseMixin, View):
    """Show audit log history with filters and pagination."""

    template_name = "frontend/audit_log/list.html"

    def get_queryset(self):
        queryset = self.firm_audit_logs().select_related("actor")
        filter_form = AuditLogFilterForm(self.request.GET or None, user=self.request.user)

        if filter_form.is_valid():
            action = filter_form.cleaned_data.get("action")
            actor = filter_form.cleaned_data.get("actor")
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

        return queryset, filter_form

    def get(self, request):
        if request.user.role == User.Role.INTERN:
            return HttpResponseForbidden("interns are not allowed")
        queryset, filter_form = self.get_queryset()
        page_obj = self.paginate_queryset(queryset, per_page=20)
        context = {
            "logs": page_obj,
            "page_obj": page_obj,
            "filter_form": filter_form,
            "search_query": request.GET.get("search", ""),
        }
        return self.render_page(request, self.template_name, context)


class MarkNotificationsReadView(FrontendBaseMixin, View):
    """Mark visible topbar notifications as read."""

    def post(self, request):
        self.firm_notifications().filter(is_read=False).update(is_read=True)
        return redirect(request.POST.get("next") or "dashboard")
