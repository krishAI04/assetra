from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0002_alter_document_file"),
    ]

    operations = [
        migrations.CreateModel(
            name="Notification",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("message", models.CharField(max_length=255)),
                (
                    "notification_type",
                    models.CharField(
                        choices=[
                            ("info", "Info"),
                            ("client", "Client"),
                            ("document", "Document"),
                            ("approval", "Approval"),
                            ("user", "User"),
                        ],
                        default="info",
                        max_length=20,
                    ),
                ),
                ("target_url", models.CharField(blank=True, max_length=255)),
                ("is_read", models.BooleanField(default=False)),
                (
                    "firm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notifications",
                        to="core.firm",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["firm", "is_read", "created_at"],
                        name="core_notifi_firm_id_18a487_idx",
                    ),
                    models.Index(
                        fields=["user", "is_read", "created_at"],
                        name="core_notifi_user_id_8690d1_idx",
                    ),
                ],
            },
        ),
    ]
