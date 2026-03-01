from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tasks.models import User


class Command(BaseCommand):
    help = "Set a temporary password for a Taskit user and require password change"

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--password", required=True)

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        password = options["password"]
        if len(password) < 6:
            raise CommandError("Password must be at least 6 characters")

        user = User.objects.filter(email=email).select_related("auth_user").first()
        if not user:
            raise CommandError(f"No Taskit user found for '{email}'")

        auth_user_model = get_user_model()
        auth_user = user.auth_user
        if auth_user is None:
            auth_user = auth_user_model.objects.filter(username=email).first()
        if auth_user is None:
            auth_user = auth_user_model(username=email, email=email, first_name=user.name)

        auth_user.set_password(password)
        auth_user.save()

        user.auth_user = auth_user
        user.must_change_password = True
        user.save(update_fields=["auth_user", "must_change_password"])
        self.stdout.write(self.style.SUCCESS(f"Temporary password set for {email}"))
