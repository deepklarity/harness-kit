from django.core.management.base import BaseCommand, CommandError

from tasks.models import User


class Command(BaseCommand):
    help = "Mark users to force password reset on next login"

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Flag all human users")
        parser.add_argument("--email", default=None, help="Flag a specific user email")

    def handle(self, *args, **options):
        do_all = options["all"]
        email = (options["email"] or "").strip().lower()

        if not do_all and not email:
            raise CommandError("Provide --all or --email")

        qs = User.objects.all()
        if do_all:
            qs = qs.exclude(role="AGENT")
        if email:
            qs = qs.filter(email=email)

        updated = qs.update(must_change_password=True)
        self.stdout.write(self.style.SUCCESS(f"Flagged {updated} user(s) for password reset"))
