from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tasks.models import User


class Command(BaseCommand):
    help = "Create a new user and linked Django auth credentials"

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument(
            "--password",
            default=None,
            help="Required when AUTH_ENABLED=True (or FIREBASE_AUTH_ENABLED compatibility flag)",
        )
        parser.add_argument(
            "--must-change-password",
            action="store_true",
            help="Force user to change password on next login",
        )

    def handle(self, *args, **options):
        name = options["name"].strip()
        email = options["email"].strip().lower()
        password = options["password"]
        must_change_password = options["must_change_password"]

        if User.objects.filter(email=email).exists():
            raise CommandError(f"User with email '{email}' already exists")

        auth_enabled = getattr(settings, "AUTH_ENABLED", False)
        if auth_enabled and not password:
            raise CommandError("--password is required when AUTH_ENABLED=True")
        if password and len(password) < 6:
            raise CommandError("Password must be at least 6 characters")

        auth_user_model = get_user_model()
        auth_user = auth_user_model.objects.filter(username=email).first()
        if auth_user is None:
            auth_user = auth_user_model(
                username=email,
                email=email,
                first_name=name,
            )

        if password:
            auth_user.set_password(password)
        else:
            auth_user.set_unusable_password()
        auth_user.save()

        user = User.objects.create(
            name=name,
            email=email,
            auth_user=auth_user,
            must_change_password=(auth_enabled or must_change_password),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created user: {user.name} <{user.email}> (id={user.id})"
            )
        )
