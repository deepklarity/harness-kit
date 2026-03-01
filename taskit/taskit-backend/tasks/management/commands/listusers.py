from django.core.management.base import BaseCommand

from tasks.models import User


class Command(BaseCommand):
    help = "List all users"

    def handle(self, *args, **options):
        users = User.objects.all().order_by("id")
        if not users.exists():
            self.stdout.write("No users found.")
            return

        self.stdout.write(f"{'ID':<6} {'Name':<30} {'Email':<40}")
        self.stdout.write("-" * 76)
        for user in users:
            self.stdout.write(f"{user.id:<6} {user.name:<30} {user.email:<40}")
