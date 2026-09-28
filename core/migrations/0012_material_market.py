import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_userprofile_site_key"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="public_market_url",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="market_site_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="encrypted_market_private_key",
            field=models.TextField(blank=True),
        ),
        migrations.CreateModel(
            name="MarketSiteCredential",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("site_id", models.UUIDField(unique=True)),
                ("name", models.CharField(max_length=120)),
                ("public_key", models.CharField(max_length=200)),
                ("status", models.CharField(choices=[("pending", "待审批"), ("approved", "已批准"), ("revoked", "已撤销")], default="pending", max_length=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["status", "name", "created_at"]},
        ),
        migrations.CreateModel(
            name="MarketListing",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source_item_id", models.CharField(blank=True, default="", max_length=80)),
                ("remote_listing_id", models.CharField(blank=True, default="", max_length=80)),
                ("scope", models.CharField(choices=[("local", "本站"), ("public", "公共")], default="local", max_length=8)),
                ("kind", models.CharField(choices=[("character", "角色卡"), ("worldbook", "世界书")], max_length=16)),
                ("title", models.CharField(max_length=120)),
                ("description", models.CharField(blank=True, max_length=500)),
                ("tags", models.JSONField(blank=True, default=list)),
                ("author_alias", models.CharField(max_length=80)),
                ("payload", models.JSONField()),
                ("status", models.CharField(choices=[("published", "已发布"), ("withdrawn", "已撤回"), ("hidden", "已隐藏")], default="published", max_length=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("origin_site", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="listings", to="core.marketsitecredential")),
                ("owner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="market_listings", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "title"]},
        ),
        migrations.AddIndex(
            model_name="marketlisting",
            index=models.Index(fields=["scope", "status", "kind", "created_at"], name="market_scope_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="marketlisting",
            constraint=models.UniqueConstraint(condition=Q(origin_site__isnull=False) & ~Q(source_item_id=""), fields=("origin_site", "source_item_id"), name="unique_market_remote_source_item"),
        ),
        migrations.CreateModel(
            name="MarketNonce",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("value", models.CharField(max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("site", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="nonces", to="core.marketsitecredential")),
            ],
        ),
        migrations.AddConstraint(
            model_name="marketnonce",
            constraint=models.UniqueConstraint(fields=("site", "value"), name="unique_market_site_nonce"),
        ),
        migrations.AddIndex(
            model_name="marketnonce",
            index=models.Index(fields=["created_at"], name="market_nonce_created_idx"),
        ),
        migrations.CreateModel(
            name="MarketReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reporter_site", models.CharField(blank=True, max_length=80)),
                ("reason", models.CharField(max_length=500)),
                ("status", models.CharField(choices=[("open", "待处理"), ("reviewed", "已处理"), ("dismissed", "已驳回")], default="open", max_length=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("handled_at", models.DateTimeField(blank=True, null=True)),
                ("listing", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reports", to="core.marketlisting")),
                ("reporter", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="market_reports", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
