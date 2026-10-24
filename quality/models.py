"""
Models and business rules for the quality control app.

The important idea: the *rules* live here, on the models, not in the views.
Views only call these methods and show the message if a RuleError comes back.
That keeps the rules in one place and makes them easy to test.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

# A supervisor must choose one of these when giving a concession.
CONCESSION_CODES = [
    ("minor_dimension", "Minor dimensional deviation - part still fits and works"),
    ("cosmetic", "Cosmetic issue only - no effect on function"),
    ("customer_ok", "Customer agreed to this deviation"),
    ("other", "Other (explain in the justification)"),
]

# The PRD says a failed value needs at least two photos as proof.
PHOTOS_NEEDED_ON_FAIL = 2


class RuleError(Exception):
    """Someone tried to do something the quality rules do not allow.

    The text of the error is written for the end user, so views can show it
    directly in a red message box.
    """


# ---------------------------------------------------------------------------
# Setup data (the admin fills these in once)
# ---------------------------------------------------------------------------

class Machine(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):
    part_number = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)

    class Meta:
        ordering = ["part_number"]

    def __str__(self):
        return f"{self.part_number} - {self.name}"


class Gauge(models.Model):
    """A measuring tool (caliper, micrometer...) with a calibration expiry date."""

    gauge_id = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    calibration_expiry = models.DateField()

    class Meta:
        ordering = ["gauge_id"]

    def is_expired(self, on=None):
        """True once the expiry date has passed.

        A gauge is still valid *on* its expiry date and expires the day after.
        """
        today = on or timezone.localdate()
        return self.calibration_expiry < today

    def __str__(self):
        return f"{self.gauge_id} - {self.name}"


class InspectionField(models.Model):
    """One thing to measure on a product, with its allowed limits.

    Example: nominal 25.000 mm, upper_tol +0.050, lower_tol -0.020
    means any value from 24.980 to 25.050 is acceptable.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="fields")
    label = models.CharField(max_length=120)
    nominal = models.DecimalField(max_digits=10, decimal_places=3)
    upper_tol = models.DecimalField(
        "upper tolerance", max_digits=10, decimal_places=3, default=Decimal("0"),
        help_text="How far ABOVE nominal is still OK, e.g. 0.050",
    )
    lower_tol = models.DecimalField(
        "lower tolerance", max_digits=10, decimal_places=3, default=Decimal("0"),
        help_text="How far BELOW nominal is still OK, written as a negative number, e.g. -0.020",
    )
    unit = models.CharField(max_length=10, default="mm")
    is_ctq = models.BooleanField(
        "critical to quality (CTQ)", default=False,
        help_text="A failed CTQ value freezes the inspection and cannot get a concession.",
    )
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["product", "label"], name="unique_field_label_per_product"),
        ]

    @property
    def upper_limit(self):
        return self.nominal + self.upper_tol

    @property
    def lower_limit(self):
        return self.nominal + self.lower_tol

    def is_within_limits(self, value):
        """Both limits are allowed values: exactly on the limit still passes."""
        value = Decimal(str(value))
        return self.lower_limit <= value <= self.upper_limit

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.lower_tol > self.upper_tol:
            raise ValidationError("The lower tolerance cannot be bigger than the upper tolerance.")

    def __str__(self):
        return f"{self.product.part_number}: {self.label}"


class Job(models.Model):
    """A production lot of one product. Inspections and shipping hang off this."""

    job_number = models.CharField(max_length=40, unique=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="jobs")
    machine = models.ForeignKey(Machine, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs")
    quantity = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.job_number

    # -- inspection helpers --------------------------------------------------

    def latest_inspection(self):
        return self.inspections.order_by("-id").first()

    def is_quarantined(self):
        return self.inspections.filter(status=Inspection.QUARANTINED).exists()

    def open_inspection(self):
        """An inspection that has been started but not decided yet."""
        return self.inspections.filter(
            status__in=[Inspection.IN_PROGRESS, Inspection.PENDING_REVIEW]
        ).first()

    # -- shipping lock ------------------------------------------------------

    def shipping_block_reason(self):
        """Return the reason this job cannot ship, or None if it is clear to ship."""
        if GatePass.objects.filter(job=self).exists():
            return "A gate pass has already been created for this job."

        if self.is_quarantined():
            return ("This job is quarantined. It cannot ship until the Material Review Board "
                    "decides what happens to it.")

        latest = self.latest_inspection()
        if latest is None:
            return "No inspection has been done for this job yet."

        if latest.status == Inspection.IN_PROGRESS:
            return "The inspection has been started but the inspector has not submitted it yet."
        if latest.status == Inspection.PENDING_REVIEW:
            return "The inspection is waiting for a supervisor's decision (Pending Review)."
        if latest.status in (Inspection.APPROVED, Inspection.CONCESSION):
            return None

        return f"The latest inspection is '{latest.get_status_display()}', so the job cannot ship."

    def create_gate_pass(self, user):
        reason = self.shipping_block_reason()
        if reason:
            raise RuleError(reason)
        return GatePass.objects.create(job=self, inspection=self.latest_inspection(), created_by=user)


# ---------------------------------------------------------------------------
# Inspection work
# ---------------------------------------------------------------------------

class Inspection(models.Model):
    IN_PROGRESS = "in_progress"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    CONCESSION = "concession"
    QUARANTINED = "quarantined"
    STATUS_CHOICES = [
        (IN_PROGRESS, "In progress"),
        (PENDING_REVIEW, "Pending review"),
        (APPROVED, "Approved"),
        (CONCESSION, "Released by concession"),
        (QUARANTINED, "Quarantined"),
    ]

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="inspections")
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="inspections")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=IN_PROGRESS)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    # Filled in when the supervisor decides.
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="reviews"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    concession_code = models.CharField(max_length=30, blank=True, choices=CONCESSION_CODES)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"Inspection #{self.pk} of {self.job}"

    # -- questions about the current state ----------------------------------

    def missing_fields(self):
        """Fields of the product that have not been measured yet."""
        return self.job.product.fields.exclude(measurements__inspection=self)

    def has_failure(self):
        return self.measurements.filter(passed=False).exists()

    def has_ctq_failure(self):
        return self.measurements.filter(passed=False, field__is_ctq=True).exists()

    # -- inspector actions --------------------------------------------------

    @classmethod
    def start(cls, job, inspector):
        if job.is_quarantined():
            raise RuleError("This job is quarantined, so it cannot be inspected again.")
        if job.open_inspection():
            raise RuleError("This job already has an open inspection.")
        if not job.product.fields.exists():
            raise RuleError("This product has no inspection fields yet. Ask an admin to add them.")
        return cls.objects.create(job=job, inspector=inspector)

    @transaction.atomic
    def record_measurement(self, field, value, gauge, photos=()):
        """Save one measurement after checking every rule.

        `photos` is a list of uploaded image files.
        """
        if self.status != self.IN_PROGRESS:
            raise RuleError("This inspection is locked. No more measurements can be entered.")
        if field.product_id != self.job.product_id:
            raise RuleError("That field does not belong to this product.")
        if self.measurements.filter(field=field).exists():
            raise RuleError(f"'{field.label}' has already been measured.")

        if gauge.is_expired():
            raise RuleError(
                f"Expired Tool Calibration: gauge {gauge.gauge_id} expired on "
                f"{gauge.calibration_expiry:%d %b %Y}. Please get a calibrated tool from the tool crib."
            )

        passed = field.is_within_limits(value)
        if not passed and len(photos) < PHOTOS_NEEDED_ON_FAIL:
            raise RuleError(
                f"{value} is outside the limits ({field.lower_limit} to {field.upper_limit} {field.unit}). "
                f"Please upload at least {PHOTOS_NEEDED_ON_FAIL} photos of the defect or gauge reading."
            )

        measurement = self.measurements.create(field=field, gauge=gauge, value=value, passed=passed)
        for image in photos:
            measurement.photos.create(image=image)

        # A failed CTQ value freezes the checklist: it goes straight to the supervisor.
        if not passed and field.is_ctq:
            self._send_for_review()

        return measurement

    def submit(self):
        if self.status != self.IN_PROGRESS:
            raise RuleError("This inspection has already been submitted.")
        missing = list(self.missing_fields())
        if missing:
            names = ", ".join(f.label for f in missing)
            raise RuleError(f"You cannot submit yet. These fields are still empty: {names}.")
        self._send_for_review()

    def _send_for_review(self):
        self.status = self.PENDING_REVIEW
        self.submitted_at = timezone.now()
        self.save(update_fields=["status", "submitted_at"])

    # -- supervisor actions -------------------------------------------------

    def _check_reviewer(self, user):
        if self.status != self.PENDING_REVIEW:
            raise RuleError("Only inspections that are 'Pending review' can be decided, and a decision is final.")
        if user.pk == self.inspector_id:
            raise RuleError("You cannot review your own inspection. Another supervisor must do it.")

    def _finish(self, user, status, note="", code=""):
        self.status = status
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.decision_note = note
        self.concession_code = code
        self.save()

    def approve(self, user, note=""):
        self._check_reviewer(user)
        if self.has_failure():
            raise RuleError(
                "You cannot approve an inspection with out-of-limit values. "
                "Use a concession (non-CTQ only) or quarantine instead."
            )
        self._finish(user, self.APPROVED, note.strip())

    def concede(self, user, code, justification):
        self._check_reviewer(user)
        if self.has_ctq_failure():
            raise RuleError("A concession is not allowed when a CTQ value failed. Quarantine is the only option.")
        if not self.has_failure():
            raise RuleError("Every value passed, so there is nothing to concede. Approve it instead.")
        if code not in dict(CONCESSION_CODES):
            raise RuleError("Please choose a concession reason code.")
        justification = (justification or "").strip()
        if len(justification) < 10:
            raise RuleError("Please write a proper engineering justification (at least 10 characters).")
        self._finish(user, self.CONCESSION, justification, code)

    def quarantine(self, user, note):
        self._check_reviewer(user)
        note = (note or "").strip()
        if len(note) < 5:
            raise RuleError("Please write why this lot is being quarantined.")
        self._finish(user, self.QUARANTINED, note)


class Measurement(models.Model):
    inspection = models.ForeignKey(Inspection, on_delete=models.CASCADE, related_name="measurements")
    field = models.ForeignKey(InspectionField, on_delete=models.PROTECT, related_name="measurements")
    gauge = models.ForeignKey(Gauge, on_delete=models.PROTECT, related_name="measurements")
    value = models.DecimalField(max_digits=10, decimal_places=3)
    passed = models.BooleanField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["field__order", "field__id"]
        constraints = [
            models.UniqueConstraint(fields=["inspection", "field"], name="one_measurement_per_field"),
        ]

    def __str__(self):
        return f"{self.field.label} = {self.value}"


class DefectPhoto(models.Model):
    measurement = models.ForeignKey(Measurement, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="defects/%Y/%m/")
    uploaded_at = models.DateTimeField(auto_now_add=True)


# ---------------------------------------------------------------------------
# Shipping
# ---------------------------------------------------------------------------

class GatePass(models.Model):
    job = models.OneToOneField(Job, on_delete=models.PROTECT, related_name="gate_pass")
    inspection = models.ForeignKey(Inspection, on_delete=models.PROTECT, related_name="gate_passes")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="gate_passes")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Gate pass GP-{self.pk:05d} for {self.job}"
