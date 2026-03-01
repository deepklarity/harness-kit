from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tasks.models import User


class Command(BaseCommand):
    help = "Create or update an admin user with Django auth credentials"

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument("--password", required=True)
        parser.add_argument(
            "--must-change-password",
            action="store_true",
            help="Force password rotation on next login",
        )

    def handle(self, *args, **options):
        name = options["name"].strip()
        email = options["email"].strip().lower()
        password = options["password"]
        must_change_password = options["must_change_password"]

        if len(password) < 6:
            raise CommandError("Password must be at least 6 characters")

        auth_user_model = get_user_model()
        auth_user = auth_user_model.objects.filter(username=email).first()
        if auth_user is None:
            auth_user = auth_user_model(
                username=email,
                email=email,
                first_name=name,
                is_staff=True,
            )
        else:
            auth_user.email = email
            auth_user.first_name = name
            auth_user.is_staff = True
        auth_user.set_password(password)
        auth_user.save()

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "name": name,
                "is_admin": True,
                "auth_user": auth_user,
                "must_change_password": must_change_password,
            },
        )
        if not created:
            user.name = name
            user.is_admin = True
            user.auth_user = auth_user
            user.must_change_password = must_change_password
            user.save(
                update_fields=["name", "is_admin", "auth_user", "must_change_password"]
            )
            self.stdout.write(f"Updated existing user to admin: {user.email}")
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created admin: {user.name} <{user.email}> (id={user.id})"
                )
            )
