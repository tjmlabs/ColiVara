from allauth.socialaccount.models import SocialApp
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "populate social app in DB with dummy values for testing"

    def handle(self, *args, **options):
        app, _ = SocialApp.objects.get_or_create(
            provider="github", name="Test-Colivara", client_id="dummy", secret="dummy"
        )

        self.stdout.write(
            self.style.SUCCESS(f"Successfully created social app {app.name}")
        )

        return
