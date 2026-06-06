from decimal import Decimal
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AIDocumentAnalysis,
    Asset,
    AssetDistribution,
    AuditLog,
    Beneficiary,
    Client,
    Document,
    Notification,
    Task,
    User,
)


class Command(BaseCommand):
    help = "Seed polished demo data for one admin user's firm."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="krish39")

    def handle(self, *args, **options):
        username = options["username"]
        admin_user = User.objects.filter(username=username).select_related("firm").first()
        if not admin_user:
            raise CommandError(f"User '{username}' was not found.")
        if not admin_user.firm:
            raise CommandError(f"User '{username}' does not belong to a firm.")

        firm = admin_user.firm
        admin_user.role = User.Role.ADMIN
        admin_user.is_active = True
        admin_user.save(update_fields=["role", "is_active"])

        clients = self.create_clients(firm)
        beneficiaries = self.create_beneficiaries(firm, clients)
        assets = self.create_assets(firm, clients)
        documents = self.create_documents(firm, admin_user, clients, assets)
        distributions = self.create_distributions(firm, admin_user, assets, beneficiaries)
        self.create_tasks(firm, admin_user, clients)
        self.create_ai_analyses(firm, admin_user, documents)
        self.create_notifications(firm, admin_user, clients, documents, distributions)
        self.create_audit_logs(firm, admin_user, clients, assets, documents, distributions)

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo data seeded for {username} in firm '{firm.name}'. "
                "Refresh the dashboard to see the populated workspace."
            )
        )

    def create_clients(self, firm):
        client_rows = [
            {
                "first_name": "Arjun",
                "last_name": "Mehra",
                "email": "arjun.mehra@example.com",
                "phone": "+91 98765 12001",
                "address": "Bandra West, Mumbai",
                "status": Client.Status.ESTATE_PROCESSING,
                "notes": "High-value property estate with multiple supporting documents.",
            },
            {
                "first_name": "Nisha",
                "last_name": "Kapoor",
                "email": "nisha.kapoor@example.com",
                "phone": "+91 98765 12002",
                "address": "Golf Course Road, Gurugram",
                "status": Client.Status.ACTIVE,
                "notes": "Active client preparing future asset distribution plan.",
            },
            {
                "first_name": "Rohan",
                "last_name": "Sethi",
                "email": "rohan.sethi@example.com",
                "phone": "+91 98765 12003",
                "address": "Indiranagar, Bengaluru",
                "status": Client.Status.ESTATE_PROCESSING,
                "notes": "Business and investment assets under review.",
            },
            {
                "first_name": "Priya",
                "last_name": "Raman",
                "email": "priya.raman@example.com",
                "phone": "+91 98765 12004",
                "address": "Adyar, Chennai",
                "status": Client.Status.CLOSED,
                "notes": "Estate workflow completed and closed.",
            },
        ]
        clients = {}
        for row in client_rows:
            client, _ = Client.objects.update_or_create(
                firm=firm,
                email=row["email"],
                defaults=row,
            )
            clients[client.email] = client
        return clients

    def create_beneficiaries(self, firm, clients):
        rows = [
            ("arjun.mehra@example.com", "Aarav", "Mehra", "Son"),
            ("arjun.mehra@example.com", "Isha", "Mehra", "Daughter"),
            ("nisha.kapoor@example.com", "Dev", "Kapoor", "Spouse"),
            ("rohan.sethi@example.com", "Maya", "Sethi", "Sister"),
            ("priya.raman@example.com", "Kavya", "Raman", "Daughter"),
        ]
        beneficiaries = {}
        for email, first_name, last_name, relationship in rows:
            client = clients[email]
            beneficiary, _ = Beneficiary.objects.update_or_create(
                firm=firm,
                client=client,
                first_name=first_name,
                last_name=last_name,
                defaults={
                    "email": f"{first_name.lower()}.{last_name.lower()}@example.com",
                    "phone": "+91 90000 12000",
                    "relationship_to_client": relationship,
                    "notes": "Demo beneficiary record for dashboard presentation.",
                },
            )
            beneficiaries[f"{email}:{first_name}"] = beneficiary
        return beneficiaries

    def create_assets(self, firm, clients):
        rows = [
            ("arjun.mehra@example.com", "Bandra Sea View Apartment", Asset.Category.PROPERTY, "Residential property under transfer review.", "28500000.00"),
            ("arjun.mehra@example.com", "HDFC Estate Savings Account", Asset.Category.BANK, "Bank account linked to estate settlement.", "1250000.00"),
            ("nisha.kapoor@example.com", "Gurugram Commercial Office", Asset.Category.PROPERTY, "Commercial office space with lease documents.", "42000000.00"),
            ("nisha.kapoor@example.com", "Family Insurance Policy", Asset.Category.INSURANCE, "Insurance policy requiring beneficiary confirmation.", "8000000.00"),
            ("rohan.sethi@example.com", "Sethi Textiles LLP Share", Asset.Category.BUSINESS, "Business ownership share awaiting legal confirmation.", "18500000.00"),
            ("rohan.sethi@example.com", "Bluechip Equity Portfolio", Asset.Category.STOCK, "Investment portfolio with nominee details.", "6400000.00"),
            ("priya.raman@example.com", "Chennai Family Home", Asset.Category.PROPERTY, "Closed estate property record.", "17500000.00"),
        ]
        assets = {}
        for email, title, category, description, value in rows:
            client = clients[email]
            asset, _ = Asset.objects.update_or_create(
                firm=firm,
                client=client,
                title=title,
                defaults={
                    "category": category,
                    "description": description,
                    "estimated_value": Decimal(value),
                    "is_active": client.status != Client.Status.CLOSED,
                },
            )
            assets[title] = asset
        return assets

    def create_documents(self, firm, admin_user, clients, assets):
        rows = [
            ("Property Ownership Agreement", clients["arjun.mehra@example.com"], assets["Bandra Sea View Apartment"]),
            ("Bank Nominee Confirmation", clients["arjun.mehra@example.com"], assets["HDFC Estate Savings Account"]),
            ("Commercial Lease Certificate", clients["nisha.kapoor@example.com"], assets["Gurugram Commercial Office"]),
            ("Insurance Beneficiary Form", clients["nisha.kapoor@example.com"], assets["Family Insurance Policy"]),
            ("LLP Shareholding Extract", clients["rohan.sethi@example.com"], assets["Sethi Textiles LLP Share"]),
            ("Equity Portfolio Statement", clients["rohan.sethi@example.com"], assets["Bluechip Equity Portfolio"]),
        ]
        documents = {}
        for title, client, asset in rows:
            document, created = Document.objects.get_or_create(
                firm=firm,
                title=title,
                defaults={
                    "client": client,
                    "asset": asset,
                    "uploaded_by": admin_user,
                },
            )
            if created or not document.file:
                document.file.save(
                    f"{title.lower().replace(' ', '-')}.pdf",
                    ContentFile(self.fake_pdf(title, client.full_name, asset.title)),
                    save=True,
                )
            documents[title] = document
        return documents

    def create_distributions(self, firm, admin_user, assets, beneficiaries):
        rows = [
            (
                assets["Bandra Sea View Apartment"],
                beneficiaries["arjun.mehra@example.com:Aarav"],
                "50.00",
                AssetDistribution.ApprovalStatus.PENDING,
            ),
            (
                assets["Bandra Sea View Apartment"],
                beneficiaries["arjun.mehra@example.com:Isha"],
                "50.00",
                AssetDistribution.ApprovalStatus.PENDING,
            ),
            (
                assets["Family Insurance Policy"],
                beneficiaries["nisha.kapoor@example.com:Dev"],
                "100.00",
                AssetDistribution.ApprovalStatus.APPROVED,
            ),
            (
                assets["Sethi Textiles LLP Share"],
                beneficiaries["rohan.sethi@example.com:Maya"],
                "40.00",
                AssetDistribution.ApprovalStatus.REJECTED,
            ),
        ]
        distributions = []
        for asset, beneficiary, percentage, status in rows:
            defaults = {
                "ownership_percentage": Decimal(percentage),
                "approval_status": status,
                "rejection_reason": "",
                "approved_by": None,
                "approved_at": None,
            }
            if status == AssetDistribution.ApprovalStatus.APPROVED:
                defaults["approved_by"] = admin_user
                defaults["approved_at"] = timezone.now()
            if status == AssetDistribution.ApprovalStatus.REJECTED:
                defaults["rejection_reason"] = "Ownership proof requires clarification."
            distribution, _ = AssetDistribution.objects.update_or_create(
                firm=firm,
                asset=asset,
                beneficiary=beneficiary,
                defaults=defaults,
            )
            distributions.append(distribution)
        return distributions

    def create_tasks(self, firm, admin_user, clients):
        rows = [
            ("Verify Bandra property signatures", clients["arjun.mehra@example.com"], Task.Priority.HIGH, Task.Status.PENDING, 3),
            ("Request missing bank nominee proof", clients["arjun.mehra@example.com"], Task.Priority.MEDIUM, Task.Status.IN_PROGRESS, 5),
            ("Review commercial lease certificate", clients["nisha.kapoor@example.com"], Task.Priority.MEDIUM, Task.Status.PENDING, 7),
            ("Prepare LLP ownership clarification", clients["rohan.sethi@example.com"], Task.Priority.HIGH, Task.Status.PENDING, 2),
            ("Archive closed estate documents", clients["priya.raman@example.com"], Task.Priority.LOW, Task.Status.COMPLETED, -1),
        ]
        for title, client, priority, status, due_offset in rows:
            Task.objects.update_or_create(
                firm=firm,
                title=title,
                defaults={
                    "client": client,
                    "description": "Demo task created to make the workspace presentation-ready.",
                    "assigned_to": admin_user,
                    "created_by": admin_user,
                    "due_date": timezone.localdate() + timedelta(days=due_offset),
                    "priority": priority,
                    "status": status,
                },
            )

    def create_ai_analyses(self, firm, admin_user, documents):
        rows = [
            documents["Property Ownership Agreement"],
            documents["Commercial Lease Certificate"],
            documents["LLP Shareholding Extract"],
        ]
        for document in rows:
            AIDocumentAnalysis.objects.update_or_create(
                firm=firm,
                document=document,
                summary=f"{document.title} appears ready for legal review with ownership and supporting proof checks.",
                defaults={
                    "created_by": admin_user,
                    "mode": AIDocumentAnalysis.Mode.LIVE,
                    "important_parties": [
                        document.client.full_name if document.client else "Client",
                        document.asset.title if document.asset else "Asset",
                    ],
                    "important_dates": [timezone.localdate().strftime("%d %b %Y")],
                    "asset_details": {
                        "asset": document.asset.title if document.asset else "Not linked",
                        "category": document.asset.get_category_display() if document.asset else "Not linked",
                        "estimated_value": str(document.asset.estimated_value) if document.asset else "0.00",
                    },
                    "risk_points": [
                        "Confirm ownership proof is complete.",
                        "Verify signature and witness details.",
                        "Match document details with asset record.",
                    ],
                    "suggested_next_action": "Assign a lawyer to verify supporting proof before approval.",
                    "raw_response": {"source": "demo_seed"},
                },
            )

    def create_notifications(self, firm, admin_user, clients, documents, distributions):
        rows = [
            ("AI analysis ready: Property Ownership Agreement", Notification.Type.DOCUMENT, reverse("documents")),
            ("New client added: Arjun Mehra", Notification.Type.CLIENT, reverse("client-workspace", kwargs={"pk": clients["arjun.mehra@example.com"].pk})),
            ("Distribution awaiting approval: Bandra Sea View Apartment", Notification.Type.APPROVAL, reverse("approvals")),
            ("Document uploaded: Commercial Lease Certificate", Notification.Type.DOCUMENT, reverse("document-preview", kwargs={"pk": documents["Commercial Lease Certificate"].pk})),
        ]
        for message, notification_type, target_url in rows:
            Notification.objects.update_or_create(
                firm=firm,
                message=message,
                defaults={
                    "user": None,
                    "notification_type": notification_type,
                    "target_url": target_url,
                    "is_read": False,
                },
            )

    def create_audit_logs(self, firm, admin_user, clients, assets, documents, distributions):
        rows = [
            ("create", "Client", clients["arjun.mehra@example.com"].pk, "Created client Arjun Mehra."),
            ("create", "Asset", assets["Bandra Sea View Apartment"].pk, "Created asset Bandra Sea View Apartment."),
            ("upload", "Document", documents["Property Ownership Agreement"].pk, "Uploaded document Property Ownership Agreement."),
            ("ai_analyze", "AIDocumentAnalysis", documents["Property Ownership Agreement"].pk, "Analyzed document Property Ownership Agreement."),
            ("create", "AssetDistribution", distributions[0].pk, "Created distribution for Bandra Sea View Apartment."),
            ("approve", "AssetDistribution", distributions[2].pk, "Approved distribution Family Insurance Policy."),
            ("reject", "AssetDistribution", distributions[3].pk, "Rejected distribution Sethi Textiles LLP Share."),
        ]
        for action, target_model, target_id, description in rows:
            AuditLog.objects.update_or_create(
                firm=firm,
                action=action,
                target_model=target_model,
                target_id=target_id,
                description=description,
                defaults={
                    "actor": admin_user,
                    "metadata": {"demo_seed": True},
                },
            )

    def fake_pdf(self, title, client_name, asset_title):
        content = (
            f"%PDF-1.4\n"
            f"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            f"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
            f"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents 4 0 R >> endobj\n"
            f"4 0 obj << /Length 120 >> stream\n"
            f"BT /F1 18 Tf 72 720 Td ({title}) Tj 0 -28 Td "
            f"(Client: {client_name}) Tj 0 -28 Td (Asset: {asset_title}) Tj ET\n"
            f"endstream endobj\n"
            f"trailer << /Root 1 0 R >>\n%%EOF"
        )
        return content.encode("utf-8")
