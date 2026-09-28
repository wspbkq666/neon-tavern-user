import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from cryptography.fernet import Fernet

from deployment.merge_site_databases import merge_site_databases


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE django_migrations (app TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY(app, name));
CREATE TABLE auth_user (
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
  password TEXT NOT NULL, is_staff INTEGER NOT NULL, is_superuser INTEGER NOT NULL
);
CREATE TABLE core_sitesettings (
  id INTEGER PRIMARY KEY, "values" TEXT NOT NULL, encrypted_api_key TEXT NOT NULL,
  public_market_url TEXT NOT NULL, market_site_id TEXT NOT NULL,
  encrypted_market_private_key TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE core_usersettings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL UNIQUE,
  overrides TEXT NOT NULL, encrypted_api_key TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES auth_user(id)
);
CREATE TABLE core_userprofile (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL UNIQUE,
  site_key TEXT NOT NULL, inferred_traits TEXT NOT NULL,
  policy_consent_at TEXT, privacy_policy_version TEXT NOT NULL, usage_rules_version TEXT NOT NULL,
  updated_at TEXT NOT NULL, FOREIGN KEY(user_id) REFERENCES auth_user(id)
);
CREATE TABLE core_character (
  id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, name TEXT NOT NULL,
  FOREIGN KEY(owner_id) REFERENCES auth_user(id)
);
CREATE TABLE core_conversation (
  id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, player_character_id TEXT NOT NULL,
  title TEXT NOT NULL, FOREIGN KEY(owner_id) REFERENCES auth_user(id),
  FOREIGN KEY(player_character_id) REFERENCES core_character(id)
);
CREATE TABLE core_message (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, speaker_id TEXT, content TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES core_conversation(id),
  FOREIGN KEY(speaker_id) REFERENCES core_character(id)
);
CREATE TABLE auth_user_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
  UNIQUE(user_id, group_id), FOREIGN KEY(user_id) REFERENCES auth_user(id)
);
CREATE TABLE django_admin_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, action TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES auth_user(id)
);
"""


class MergeSiteDatabasesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source_path = self.root / "source.sqlite3"
        self.target_path = self.root / "target.sqlite3"
        self.source_key = Fernet.generate_key()
        self.target_key = Fernet.generate_key()
        self._create_database(self.source_path)
        self._create_database(self.target_path)
        self._seed_source()
        self._seed_target()

    def tearDown(self):
        self.directory.cleanup()

    def _create_database(self, path):
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript(SCHEMA)
            connection.execute("INSERT INTO django_migrations VALUES ('core', '0014')")
            connection.execute(
                "INSERT INTO core_sitesettings VALUES (1, ?, '', '', ?, '', '2026-01-01')",
                (json.dumps({"shared": "site"}), f"site-{path.stem}"),
            )

    def _seed_source(self):
        source_cipher = Fernet(self.source_key)
        with closing(sqlite3.connect(self.source_path)) as connection, connection:
            connection.execute("INSERT INTO auth_user VALUES (4, 'wspbkq', 'source-hash', 1, 0)")
            connection.execute("INSERT INTO auth_user VALUES (9, 'wspkq', 'admin-hash', 1, 1)")
            connection.execute("INSERT INTO auth_user VALUES (12, 'friend', 'friend-hash', 0, 0)")
            connection.execute(
                "INSERT INTO core_usersettings(user_id, overrides, encrypted_api_key, updated_at) VALUES (4, ?, ?, '2026-02-01')",
                (json.dumps({"source": True, "shared": "source"}), source_cipher.encrypt(b"private-api-key").decode()),
            )
            connection.execute(
                "INSERT INTO core_userprofile(user_id, site_key, inferred_traits, policy_consent_at, privacy_policy_version, usage_rules_version, updated_at) VALUES (4, 'source-host', 'source trait', NULL, '', '', '2026-02-01')"
            )
            connection.execute("INSERT INTO core_character VALUES ('char-source', 4, '角色')")
            connection.execute("INSERT INTO core_conversation VALUES ('conversation-source', 4, 'char-source', '故事')")
            connection.execute("INSERT INTO core_message VALUES ('message-source', 'conversation-source', 'char-source', '正文')")
            connection.execute("INSERT INTO auth_user_groups(user_id, group_id) VALUES (9, 3)")
            connection.execute("INSERT INTO django_admin_log(user_id, action) VALUES (9, 'source audit')")
            settings = {"source-default": True, "shared": "source"}
            connection.execute('UPDATE core_sitesettings SET "values" = ?', (json.dumps(settings),))
            connection.execute(
                "UPDATE core_sitesettings SET encrypted_api_key = ?",
                (source_cipher.encrypt(b"site-api-key").decode(),),
            )

    def _seed_target(self):
        with closing(sqlite3.connect(self.target_path)) as connection, connection:
            connection.execute("INSERT INTO auth_user VALUES (1, 'wspbkq', 'target-hash', 1, 0)")
            connection.execute(
                "INSERT INTO core_usersettings(user_id, overrides, encrypted_api_key, updated_at) VALUES (1, ?, '', '2026-01-01')",
                (json.dumps({"target": True, "shared": "target"}),),
            )
            connection.execute(
                "INSERT INTO core_userprofile(user_id, site_key, inferred_traits, policy_consent_at, privacy_policy_version, usage_rules_version, updated_at) VALUES (1, 'target-host', '', NULL, '', '', '2026-01-01')"
            )
            connection.execute('UPDATE core_sitesettings SET "values" = ?', (json.dumps({"target-default": True, "shared": "target"}),))
            connection.execute("UPDATE core_sitesettings SET market_site_id = 'target-site'")
            connection.execute("INSERT INTO django_admin_log(user_id, action) VALUES (1, 'target audit')")

    def _read(self, path, sql, params=()):
        with closing(sqlite3.connect(path)) as connection, connection:
            return connection.execute(sql, params).fetchall()

    def test_dry_run_reports_plan_without_writing(self):
        report = merge_site_databases(
            self.source_path, self.target_path, self.source_key, self.target_key, apply=False
        )

        self.assertEqual(report["source_accounts"], 3)
        self.assertEqual(report["new_accounts"], 2)
        self.assertEqual(report["matched_accounts"], 1)
        self.assertEqual(self._read(self.target_path, "SELECT username FROM auth_user"), [("wspbkq",)])

    def test_apply_merges_content_and_reencrypts_keys_without_changing_target_login(self):
        merge_site_databases(
            self.source_path, self.target_path, self.source_key, self.target_key, apply=True
        )

        self.assertEqual(
            set(self._read(self.target_path, "SELECT username FROM auth_user")),
            {("wspbkq",), ("wspkq",), ("friend",)},
        )
        self.assertEqual(self._read(self.target_path, "SELECT password FROM auth_user WHERE username='wspbkq'"), [("target-hash",)])
        self.assertEqual(self._read(self.target_path, "SELECT count(*) FROM core_message"), [(1,)])
        self.assertEqual(self._read(self.target_path, "SELECT count(*) FROM django_admin_log"), [(2,)])

        row = self._read(self.target_path, "SELECT overrides, encrypted_api_key FROM core_usersettings WHERE user_id=1")[0]
        self.assertEqual(json.loads(row[0]), {"source": True, "target": True, "shared": "target"})
        self.assertEqual(Fernet(self.target_key).decrypt(row[1].encode()), b"private-api-key")

        profile = self._read(self.target_path, "SELECT site_key, inferred_traits, policy_consent_at FROM core_userprofile WHERE user_id=1")[0]
        self.assertEqual(profile, ("target-host", "source trait", None))

        values, market_site_id, encrypted_key = self._read(
            self.target_path, 'SELECT "values", market_site_id, encrypted_api_key FROM core_sitesettings'
        )[0]
        self.assertEqual(json.loads(values), {"source-default": True, "target-default": True, "shared": "target"})
        self.assertEqual(market_site_id, "target-site")
        self.assertEqual(Fernet(self.target_key).decrypt(encrypted_key.encode()), b"site-api-key")

        self.assertEqual(self._read(self.target_path, "PRAGMA integrity_check"), [("ok",)])
        self.assertEqual(self._read(self.target_path, "PRAGMA foreign_key_check"), [])

    def test_invalid_source_key_rolls_back_every_change(self):
        invalid_key = Fernet.generate_key()
        with self.assertRaises(Exception):
            merge_site_databases(
                self.source_path, self.target_path, invalid_key, self.target_key, apply=True
            )

        self.assertEqual(self._read(self.target_path, "SELECT username FROM auth_user"), [("wspbkq",)])
        self.assertEqual(self._read(self.target_path, "SELECT count(*) FROM core_character"), [(0,)])


if __name__ == "__main__":
    unittest.main()
