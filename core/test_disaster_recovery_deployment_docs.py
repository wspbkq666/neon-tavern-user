from pathlib import Path

from django.test import SimpleTestCase


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class DisasterRecoveryDeploymentDocumentationTests(SimpleTestCase):
    def _read(self, relative_path):
        return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    def test_private_ca_guide_covers_key_boundary_tls_validation_and_rotation(self):
        guide = self._read("deployment/systemd/neon-tavern-witness-private-ca.md")
        self.assertIn("hbe3.wch1.top", guide)
        self.assertIn("SAN", guide)
        self.assertIn("根 CA 私钥", guide)
        self.assertIn("TAVERN_WITNESS_CA_BUNDLE", guide)
        self.assertIn("verify=False", guide)
        self.assertIn("轮换", guide)
        self.assertIn("43111", guide)
        self.assertIn("43511", guide)

    def test_witness_service_terminates_private_ca_tls_without_shared_proxy(self):
        service = self._read("deployment/systemd/neon-tavern-witness.service.example")
        self.assertIn("--bind 0.0.0.0:443", service)
        self.assertIn("--certfile", service)
        self.assertIn("--keyfile", service)
        self.assertIn("CAP_NET_BIND_SERVICE", service)

    def test_witness_tunnel_is_loopback_only_and_key_is_forwarding_restricted(self):
        service = self._read("deployment/systemd/neon-tavern-witness-tunnel.service.example")
        self.assertIn("127.0.0.1:43111:127.0.0.1:443", service)
        self.assertIn("StrictHostKeyChecking=yes", service)
        self.assertIn("HostKeyAlias=neon-witness", service)
        self.assertIn("UserKnownHostsFile=/etc/neon-tavern-dr/witness_known_hosts", service)

    def test_tls_environment_example_contains_paths_only(self):
        sample = self._read("deployment/systemd/neon-tavern-witness-tls.env.example")
        self.assertIn("WITNESS_TLS_CERT=", sample)
        self.assertIn("WITNESS_TLS_KEY=", sample)
        self.assertNotIn("BEGIN PRIVATE KEY", sample)

    def test_production_preflight_preserves_existing_services_and_stops_on_unknown_tls(self):
        preflight = self._read("deployment/two-site-dr-production-preflight.md")
        for expected in (
            "154",
            "123",
            "witness",
            "43111",
            "43511",
            "Node",
            "443",
            "只读",
            "停止",
            "备份",
            "不得重启",
        ):
            self.assertIn(expected, preflight)
        self.assertIn("Agri Ledger", preflight)
        self.assertIn("NapCat", preflight)
        self.assertIn("最新外部只读探测（2026-09-25）", preflight)
        self.assertIn("witness 已使用用户提供的 SSH 凭据完成交互式登录", preflight)
        self.assertIn("本轮经 123 SSH 跳转完成 154 只读复核", preflight)
        self.assertIn("外部客户端未完成 TLS 握手", preflight)
        self.assertIn("公网请求被 Vite SPA 回退为农批记账首页", preflight)
        self.assertIn("154 与 123 分别通过公网 SSH `43653` 建立到 witness 的持久隧道", preflight)
        self.assertIn("业务应用 `.env` 尚未配置，容灾与数据同步仍关闭", preflight)
        self.assertIn("阶段 C 的浏览器入口仍受阻", preflight)

    def test_rollback_checklist_requires_backup_syntax_check_and_reload_only(self):
        rollback = self._read("deployment/two-site-dr-rollback-checklist.md")
        for expected in (
            "配置备份",
            "语法检查",
            "reload",
            "禁止 restart",
            "服务健康检查",
            "保留",
            "当前主站",
        ):
            self.assertIn(expected, rollback)
