"""manage.py seed_demo: fill the database with a fictional demo hospital.

Thin on purpose: options, safety checks and the summary live here; the scenarios are
in demo/seed.py. The password is never printed.
"""

import os
import time

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from demo import seed


class Command(BaseCommand):
    help = "Create (or top up) the demo dataset: users, doctors, patients and a month of activity."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            help="Password for new demo users. Prefer the DEMO_PASSWORD environment variable "
            "(a command-line argument can show up in the process list and shell history).",
        )
        parser.add_argument(
            "--reset-passwords",
            action="store_true",
            help="Also set this password on demo users that already exist.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirm seeding a database that isn't the local SQLite one.",
        )
        parser.add_argument("--seed", type=int, default=42, help="Random seed (default 42).")
        parser.add_argument(
            "--scale",
            type=float,
            default=1.0,
            help="Size of the dataset, 0.1-1.0 (default 1.0; tests use a small scale).",
        )

    def handle(self, *args, **options):
        password = options["password"] or os.environ.get("DEMO_PASSWORD", "")
        if not password:
            raise CommandError(
                "No demo password: set the DEMO_PASSWORD environment variable or pass --password."
            )
        try:
            validate_password(password)
        except ValidationError as error:
            # "from None": the validator messages are the whole story (and never the password).
            raise CommandError(
                "The demo password is too weak: " + " ".join(error.messages)
            ) from None
        if not 0.1 <= options["scale"] <= 1.0:
            raise CommandError("--scale must be between 0.1 and 1.0.")

        engine, host = seed.database_engine()
        if "sqlite" not in engine:
            self.stdout.write(f"Database: {engine.rsplit('.', 1)[-1]} on {host}")
            if not options["yes"]:
                raise CommandError(
                    f"This is not the local SQLite database (host {host}). "
                    "Re-run with --yes if you really want to seed it."
                )

        started = time.monotonic()
        seeder = seed.Seeder(
            password=password,
            reset_passwords=options["reset_passwords"],
            seed=options["seed"],
            scale=options["scale"],
        )
        try:
            report = seeder.run()
        except seed.SeedError as error:
            raise CommandError(f"Seeding stopped: {error}") from error
        self.print_report(report, time.monotonic() - started)

    def print_report(self, report, seconds):
        write = self.stdout.write
        rows = report.categories()
        width = max([len("Category"), *(len(name) for name, _, _ in rows)])
        write("")
        write(f"{'Category':<{width}}  {'Created':>7}  {'Skipped':>7}")
        write(f"{'-' * width}  {'-' * 7}  {'-' * 7}")
        for name, created, skipped in rows:
            write(f"{name:<{width}}  {created:>7}  {skipped:>7}")
        write("")
        write("Demo users (password: the one you supplied; it is not shown):")
        for username, role in report.users:
            write(f"  {username:<20} {role}")
        if report.warnings:
            write("")
            write("Warnings:")
            for warning in report.warnings:
                write(f"  - {warning}")
        write("")
        write(self.style.SUCCESS(f"Done in {seconds:.1f} s."))
