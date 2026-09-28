import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.site_federation_client import FederationClientError, enroll_with_centrals
from core.site_command_signing import SigningKeyUnavailable


class Command(BaseCommand):
    help = "将本站自动登记到霓虹酒馆总站；已登记时安全幂等"

    def handle(self, *args, **options):
        if settings.TAVERN_FEDERATION_ROLE != "satellite":
            self.stdout.write("当前部署为总站，无需登记。")
            return
        try:
            identities = enroll_with_centrals(
                settings.TAVERN_FEDERATION_SITE_NAME,
                settings.TAVERN_FEDERATION_SITE_URL,
            )
        except (FederationClientError, SigningKeyUnavailable, httpx.HTTPError) as exc:
            raise CommandError("本站未能完成总站登记；为避免出现未受监管的分站，服务不会开放。") from exc
        for target_key, identity in identities.items():
            self.stdout.write(f"{target_key} 登记完成：{identity.site_id}")
