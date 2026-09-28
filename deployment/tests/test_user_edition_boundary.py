import json
import tempfile
import unittest
from pathlib import Path

from deployment.verify_user_edition import inspect_release


class UserEditionBoundaryTests(unittest.TestCase):
    def make_release(self, root: Path) -> None:
        (root / "deployment").mkdir(parents=True)
        (root / "core").mkdir()
        (root / "frontend_dist").mkdir()
        (root / "deployment/user-edition-release.json").write_text(
            json.dumps(
                {
                    "edition": "user",
                    "global_admin_code": "excluded",
                    "federation_required": True,
                    "federation_centers": ["154.222.26.47", "123.56.125.209"],
                }
            ),
            encoding="utf-8",
        )
        (root / "core/auth_api.py").write_text(
            "role = 'site_admin'\n", encoding="utf-8"
        )
        (root / "core/settings_api.py").write_text(
            "path = '/api/admin/defaults/'\n", encoding="utf-8"
        )
        (root / "frontend_dist/admin.html").write_text(
            "<section>本站管理员</section>\n", encoding="utf-8"
        )

    def test_clean_release_keeps_site_admin_features(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_release(root)

            self.assertEqual(inspect_release(root), [])

    def test_rejects_global_admin_backend_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_release(root)
            (root / "core/auth_api.py").write_text(
                "return is_global_admin(user)\n", encoding="utf-8"
            )

            self.assertTrue(
                any("core/auth_api.py" in issue for issue in inspect_release(root))
            )

    def test_rejects_global_admin_frontend_and_control_routes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_release(root)
            (root / "frontend_dist/admin.html").write_text(
                '<section data-global-admin-only>总站更新</section>', encoding="utf-8"
            )
            (root / "core/urls.py").write_text(
                "path('/api/admin/sites/', view)", encoding="utf-8"
            )

            issues = inspect_release(root)
            self.assertTrue(any("frontend_dist/admin.html" in issue for issue in issues))
            self.assertTrue(any("core/urls.py" in issue for issue in issues))

    def test_rejects_missing_or_incomplete_user_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_release(root)
            (root / "deployment/user-edition-release.json").write_text(
                '{"edition":"user","global_admin_code":"included"}',
                encoding="utf-8",
            )

            self.assertTrue(
                any("清单" in issue for issue in inspect_release(root))
            )

    def test_checks_root_level_core_tree_not_only_backend_subdirectory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_release(root)
            (root / "core/auth_api.py").write_text(
                "role = global_admin\n", encoding="utf-8"
            )

            self.assertTrue(inspect_release(root))

    def test_current_repository_has_no_global_admin_capability(self):
        root = Path(__file__).resolve().parents[2]

        self.assertEqual(inspect_release(root), [])


if __name__ == "__main__":
    unittest.main()
