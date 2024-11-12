from django.core.management.base import BaseCommand
from accounts.models import CustomUser


class Command(BaseCommand):
    help = "Delete users with pw prefix"

    def handle(self, *args, **options):
        users = CustomUser.objects.filter(email__startswith="pw")
        num_deleted = users.count()
        users.delete()

        self.stdout.write(
            self.style.SUCCESS(f"Successfully deleted {num_deleted} users")
        )

        return
