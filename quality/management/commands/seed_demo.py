"""
Fills the database with demo data so you can try the app straight away.

    python manage.py seed_demo

It is safe to run more than once: it never creates duplicates and never
resets the password of a user that already exists.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.utils import timezone

from quality.models import Gauge, InspectionField, Job, Machine, Product

DEMO_PASSWORD = "demo1234"  # demo data only, never use this for real accounts

USERS = [
    # username, group (None = admin)
    ("inspector1", "Inspector"),
    ("inspector2", "Inspector"),
    ("supervisor1", "Supervisor"),
    ("supervisor2", "Supervisor"),
    ("clerk1", "Warehouse Clerk"),
]

# part number, name, [(label, nominal, upper tol, lower tol, unit, is_ctq)]
PRODUCTS = [
    ("SH-101", "Drive shaft", [
        ("Shaft diameter", "25.000", "0.050", "-0.020", "mm", True),
        ("Overall length", "150.000", "0.200", "-0.200", "mm", False),
        ("Surface roughness (max 0.8)", "0.400", "0.400", "-0.400", "um", False),
    ]),
    ("GR-202", "Spur gear", [
        ("Bore diameter", "12.000", "0.018", "0.000", "mm", True),
        ("Tooth width", "8.000", "0.100", "-0.100", "mm", False),
    ]),
]


class Command(BaseCommand):
    help = "Create demo users, products, gauges, machines and jobs."

    def handle(self, *args, **options):
        today = timezone.localdate()

        for username, group_name in USERS:
            user, created = User.objects.get_or_create(username=username)
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save()
            user.groups.add(Group.objects.get(name=group_name))

        admin, created = User.objects.get_or_create(
            username="admin", defaults={"is_staff": True, "is_superuser": True}
        )
        if created:
            admin.set_password("admin1234")
            admin.save()

        lathe, _ = Machine.objects.get_or_create(name="CNC Lathe #04")
        vmc, _ = Machine.objects.get_or_create(name="VMC Station #02")

        products = {}
        for part_number, name, fields in PRODUCTS:
            product, _ = Product.objects.get_or_create(part_number=part_number, defaults={"name": name})
            products[part_number] = product
            for order, (label, nominal, upper, lower, unit, ctq) in enumerate(fields):
                InspectionField.objects.get_or_create(
                    product=product, label=label,
                    defaults={
                        "nominal": Decimal(nominal), "upper_tol": Decimal(upper), "lower_tol": Decimal(lower),
                        "unit": unit, "is_ctq": ctq, "order": order,
                    },
                )

        Gauge.objects.get_or_create(gauge_id="VC-104", defaults={
            "name": "Vernier caliper", "calibration_expiry": today + timedelta(days=180)})
        Gauge.objects.get_or_create(gauge_id="MIC-07", defaults={
            "name": "Micrometer 0-25 mm", "calibration_expiry": today + timedelta(days=90)})
        # Expired on purpose, so you can see the "Expired Tool Calibration" block.
        Gauge.objects.get_or_create(gauge_id="PG-04", defaults={
            "name": "Thread plug gauge", "calibration_expiry": today - timedelta(days=10)})

        jobs = [
            ("JOB-1001", "SH-101", lathe, 50),
            ("JOB-1002", "SH-101", lathe, 30),
            ("JOB-1003", "GR-202", vmc, 120),
            ("JOB-1004", "GR-202", vmc, 80),
        ]
        for number, part, machine, qty in jobs:
            Job.objects.get_or_create(job_number=number, defaults={
                "product": products[part], "machine": machine, "quantity": qty})

        self.stdout.write(self.style.SUCCESS("Demo data is ready."))
        self.stdout.write(f"  Users (password '{DEMO_PASSWORD}'): " + ", ".join(u for u, _ in USERS))
        self.stdout.write("  Admin: admin / admin1234  (demo only)")
