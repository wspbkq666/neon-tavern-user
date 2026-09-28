from io import StringIO
import sqlite3
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings


class DisasterRecoveryInitializeTests(TransactionTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "replica"
        self.media = Path(self.temporary.name) / "media"
        self.media.mkdir()
        (self.media / "avatar.png").write_bytes(b"synthetic media")
        self.override = override_settings(
            TAVERN_DR_ENABLED=False,
            TAVERN_DR_USE_REPLICA_ACTIVE=False,
            TAVERN_DR_REPLICA_ROOT=self.root,
            MEDIA_ROOT=self.media,
        )
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        self.temporary.cleanup()

    def test_initializes_consistent_database_and_media_copy(self):
        get_user_model().objects.create_user(username="synthetic-user", password="SyntheticPass123!")

        call_command("initialize_dr_active", confirm_maintenance=True, stdout=StringIO())

        active = self.root / "active"
        database = sqlite3.connect(active / "database.sqlite3")
        try:
            count = database.execute("SELECT COUNT(*) FROM auth_user WHERE username = ?", ("synthetic-user",)).fetchone()[0]
        finally:
            database.close()
        self.assertEqual(count, 1)
        self.assertEqual((active / "media" / "avatar.png").read_bytes(), b"synthetic media")

    def test_refuses_to_replace_existing_active_copy(self):
        active = self.root / "active"
        active.mkdir(parents=True)
        marker = active / "keep.txt"
        marker.write_text("keep", encoding="utf-8")

        with self.assertRaises(CommandError):
            call_command("initialize_dr_active", confirm_maintenance=True)

        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    @override_settings(TAVERN_DR_ENABLED=True)
    def test_refuses_to_run_after_disaster_recovery_is_enabled(self):
        with self.assertRaises(CommandError):
            call_command("initialize_dr_active", confirm_maintenance=True)

    def test_requires_explicit_maintenance_confirmation(self):
        with self.assertRaises(CommandError):
            call_command("initialize_dr_active")
