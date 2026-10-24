"""
Tests for the quality rules.

Run them with:  python manage.py test

They are grouped by feature, in the same order as the app:
gauges -> limits -> measurements -> supervisor decisions -> shipping lock -> pages.
"""

import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .models import Gauge, GatePass, Inspection, InspectionField, Job, Product, RuleError

# Uploaded test photos go in a temporary folder that is deleted afterwards.
TEMP_MEDIA = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(TEMP_MEDIA, ignore_errors=True)


def make_photo(name="defect.png"):
    buffer = BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def two_photos():
    return [make_photo("a.png"), make_photo("b.png")]


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class QualityTestCase(TestCase):
    """Shared setup: users for each role, one product, three gauges and one job."""

    PASSWORD = "pw-12345-test"

    @classmethod
    def make_user(cls, username, group):
        user = User.objects.create_user(username, password=cls.PASSWORD)
        user.groups.add(Group.objects.get(name=group))
        return user

    @classmethod
    def setUpTestData(cls):
        cls.inspector = cls.make_user("ines", "Inspector")
        cls.other_inspector = cls.make_user("omar", "Inspector")
        cls.supervisor = cls.make_user("sam", "Supervisor")
        cls.supervisor2 = cls.make_user("sara", "Supervisor")
        cls.clerk = cls.make_user("carl", "Warehouse Clerk")

        today = timezone.localdate()
        cls.good_gauge = Gauge.objects.create(
            gauge_id="G-OK", name="Good caliper", calibration_expiry=today + timedelta(days=30))
        cls.expired_gauge = Gauge.objects.create(
            gauge_id="G-OLD", name="Old caliper", calibration_expiry=today - timedelta(days=1))
        cls.last_day_gauge = Gauge.objects.create(
            gauge_id="G-TODAY", name="Expires today", calibration_expiry=today)

        cls.product = Product.objects.create(part_number="P-1", name="Test shaft")
        # Allowed range 24.980 to 25.050, and this one is CTQ.
        cls.diameter = InspectionField.objects.create(
            product=cls.product, label="Diameter", nominal=Decimal("25.000"),
            upper_tol=Decimal("0.050"), lower_tol=Decimal("-0.020"), is_ctq=True, order=1)
        # Allowed range 99.5 to 100.5, normal (non-CTQ).
        cls.length = InspectionField.objects.create(
            product=cls.product, label="Length", nominal=Decimal("100.000"),
            upper_tol=Decimal("0.500"), lower_tol=Decimal("-0.500"), order=2)

        cls.job = Job.objects.create(job_number="J-1", product=cls.product, quantity=10)

    # -- small helpers so each test reads like a sentence --------------------

    def start(self, job=None, inspector=None):
        return Inspection.start(job or self.job, inspector or self.inspector)

    def measure(self, inspection, field, value, photos=None, gauge=None):
        return inspection.record_measurement(
            field, Decimal(value), gauge or self.good_gauge, photos if photos is not None else [])

    def submitted_inspection(self, diameter="25.000", length="100.000", job=None):
        """An inspection with everything measured and sent to the supervisor."""
        inspection = self.start(job)
        photos_needed = lambda ok: [] if ok else two_photos()  # noqa: E731
        self.measure(inspection, self.diameter, diameter,
                     photos_needed(self.diameter.is_within_limits(diameter)))
        if inspection.status == Inspection.IN_PROGRESS:
            self.measure(inspection, self.length, length,
                         photos_needed(self.length.is_within_limits(length)))
            inspection.submit()
        return inspection


# ---------------------------------------------------------------------------
# Feature 1: gauges
# ---------------------------------------------------------------------------

class GaugeTests(QualityTestCase):
    def test_gauge_that_expired_yesterday_is_expired(self):
        self.assertTrue(self.expired_gauge.is_expired())

    def test_gauge_is_still_valid_on_its_expiry_date(self):
        self.assertFalse(self.last_day_gauge.is_expired())

    def test_gauge_with_future_date_is_valid(self):
        self.assertFalse(self.good_gauge.is_expired())


# ---------------------------------------------------------------------------
# Feature 2: limits and measurements
# ---------------------------------------------------------------------------

class LimitTests(QualityTestCase):
    def test_limits_are_worked_out_from_nominal_and_tolerances(self):
        self.assertEqual(self.diameter.upper_limit, Decimal("25.050"))
        self.assertEqual(self.diameter.lower_limit, Decimal("24.980"))

    def test_value_exactly_on_upper_limit_passes(self):
        self.assertTrue(self.diameter.is_within_limits("25.050"))

    def test_value_exactly_on_lower_limit_passes(self):
        self.assertTrue(self.diameter.is_within_limits("24.980"))

    def test_value_just_above_upper_limit_fails(self):
        self.assertFalse(self.diameter.is_within_limits("25.051"))

    def test_value_just_below_lower_limit_fails(self):
        self.assertFalse(self.diameter.is_within_limits("24.979"))

    def test_trailing_zeros_make_no_difference(self):
        self.assertTrue(self.diameter.is_within_limits("25.0500"))

    def test_lower_tolerance_bigger_than_upper_is_rejected(self):
        bad = InspectionField(product=self.product, label="Bad", nominal=Decimal("10"),
                              upper_tol=Decimal("-0.1"), lower_tol=Decimal("0.1"))
        with self.assertRaises(ValidationError):
            bad.clean()


class MeasurementTests(QualityTestCase):
    def test_passing_value_is_saved_as_passed_and_needs_no_photos(self):
        inspection = self.start()
        measurement = self.measure(inspection, self.diameter, "25.000")
        self.assertTrue(measurement.passed)
        self.assertEqual(measurement.photos.count(), 0)

    def test_expired_gauge_blocks_the_entry(self):
        inspection = self.start()
        with self.assertRaisesMessage(RuleError, "Expired Tool Calibration"):
            self.measure(inspection, self.diameter, "25.000", gauge=self.expired_gauge)
        self.assertEqual(inspection.measurements.count(), 0)

    def test_gauge_can_still_be_used_on_its_last_valid_day(self):
        inspection = self.start()
        self.measure(inspection, self.diameter, "25.000", gauge=self.last_day_gauge)
        self.assertEqual(inspection.measurements.count(), 1)

    def test_failed_value_without_photos_is_rejected(self):
        inspection = self.start()
        with self.assertRaisesMessage(RuleError, "at least 2 photos"):
            self.measure(inspection, self.length, "101.0")
        self.assertEqual(inspection.measurements.count(), 0)

    def test_failed_value_with_only_one_photo_is_rejected(self):
        inspection = self.start()
        with self.assertRaises(RuleError):
            self.measure(inspection, self.length, "101.0", photos=[make_photo()])

    def test_failed_value_with_two_photos_is_saved_as_failed(self):
        inspection = self.start()
        measurement = self.measure(inspection, self.length, "101.0", photos=two_photos())
        self.assertFalse(measurement.passed)
        self.assertEqual(measurement.photos.count(), 2)

    def test_same_field_cannot_be_measured_twice(self):
        inspection = self.start()
        self.measure(inspection, self.diameter, "25.000")
        with self.assertRaises(RuleError):
            self.measure(inspection, self.diameter, "25.010")

    def test_ctq_failure_freezes_the_checklist_and_sends_it_to_the_supervisor(self):
        inspection = self.start()
        self.measure(inspection, self.diameter, "25.500", photos=two_photos())
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, Inspection.PENDING_REVIEW)
        with self.assertRaisesMessage(RuleError, "locked"):
            self.measure(inspection, self.length, "100.0")

    def test_normal_failure_does_not_freeze_the_checklist(self):
        inspection = self.start()
        self.measure(inspection, self.length, "101.0", photos=two_photos())
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, Inspection.IN_PROGRESS)

    def test_cannot_submit_with_empty_fields(self):
        inspection = self.start()
        self.measure(inspection, self.diameter, "25.000")
        with self.assertRaisesMessage(RuleError, "Length"):
            inspection.submit()

    def test_can_submit_when_every_field_is_measured(self):
        inspection = self.submitted_inspection()
        self.assertEqual(inspection.status, Inspection.PENDING_REVIEW)
        self.assertIsNotNone(inspection.submitted_at)

    def test_no_measurements_after_submit(self):
        inspection = self.submitted_inspection()
        with self.assertRaises(RuleError):
            self.measure(inspection, self.diameter, "25.000")

    def test_job_cannot_have_two_open_inspections(self):
        self.start()
        with self.assertRaisesMessage(RuleError, "already has an open inspection"):
            self.start(inspector=self.other_inspector)

    def test_quarantined_job_cannot_be_inspected_again(self):
        inspection = self.submitted_inspection(diameter="25.500")
        inspection.quarantine(self.supervisor, "Diameter way too big")
        with self.assertRaisesMessage(RuleError, "quarantined"):
            self.start()


# ---------------------------------------------------------------------------
# Feature 3: supervisor decisions
# ---------------------------------------------------------------------------

class SupervisorDecisionTests(QualityTestCase):
    def test_all_passed_can_be_approved(self):
        inspection = self.submitted_inspection()
        inspection.approve(self.supervisor)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, Inspection.APPROVED)
        self.assertEqual(inspection.reviewed_by, self.supervisor)
        self.assertIsNotNone(inspection.reviewed_at)

    def test_cannot_approve_when_a_value_failed(self):
        inspection = self.submitted_inspection(length="101.0")
        with self.assertRaises(RuleError):
            inspection.approve(self.supervisor)

    def test_concession_works_for_a_normal_failure(self):
        inspection = self.submitted_inspection(length="101.0")
        inspection.concede(self.supervisor, "cosmetic", "Part is only 0.5 mm long, fits fine")
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, Inspection.CONCESSION)
        self.assertEqual(inspection.concession_code, "cosmetic")
        self.assertIn("fits fine", inspection.decision_note)

    def test_concession_needs_a_reason_code(self):
        inspection = self.submitted_inspection(length="101.0")
        with self.assertRaisesMessage(RuleError, "reason code"):
            inspection.concede(self.supervisor, "", "A proper justification here")

    def test_concession_needs_a_justification(self):
        inspection = self.submitted_inspection(length="101.0")
        with self.assertRaisesMessage(RuleError, "justification"):
            inspection.concede(self.supervisor, "cosmetic", "  ok ")

    def test_concession_is_blocked_when_a_ctq_value_failed(self):
        inspection = self.submitted_inspection(diameter="25.500")
        with self.assertRaisesMessage(RuleError, "CTQ"):
            inspection.concede(self.supervisor, "cosmetic", "Looks fine to me honestly")

    def test_concession_is_blocked_when_nothing_failed(self):
        inspection = self.submitted_inspection()
        with self.assertRaisesMessage(RuleError, "nothing to concede"):
            inspection.concede(self.supervisor, "cosmetic", "No reason really needed")

    def test_ctq_failure_can_only_be_quarantined(self):
        inspection = self.submitted_inspection(diameter="25.500")
        with self.assertRaises(RuleError):
            inspection.approve(self.supervisor)
        inspection.quarantine(self.supervisor, "CTQ diameter out of limits")
        self.assertEqual(inspection.status, Inspection.QUARANTINED)

    def test_quarantine_needs_a_note(self):
        inspection = self.submitted_inspection(length="101.0")
        with self.assertRaises(RuleError):
            inspection.quarantine(self.supervisor, "")

    def test_a_decision_is_final(self):
        inspection = self.submitted_inspection(diameter="25.500")
        inspection.quarantine(self.supervisor, "CTQ diameter out of limits")
        with self.assertRaises(RuleError):
            inspection.approve(self.supervisor2)
        with self.assertRaises(RuleError):
            inspection.quarantine(self.supervisor2, "Trying again")

    def test_approved_inspection_cannot_be_quarantined_later(self):
        inspection = self.submitted_inspection()
        inspection.approve(self.supervisor)
        with self.assertRaises(RuleError):
            inspection.quarantine(self.supervisor2, "Changed my mind")

    def test_cannot_review_your_own_inspection(self):
        inspection = self.submitted_inspection()
        with self.assertRaisesMessage(RuleError, "own inspection"):
            inspection.approve(self.inspector)

    def test_cannot_decide_an_inspection_that_is_not_submitted(self):
        inspection = self.start()
        with self.assertRaises(RuleError):
            inspection.approve(self.supervisor)


# ---------------------------------------------------------------------------
# Feature 4: the shipping lock
# ---------------------------------------------------------------------------

class ShippingLockTests(QualityTestCase):
    def test_job_without_inspection_is_blocked(self):
        with self.assertRaisesMessage(RuleError, "No inspection has been done"):
            self.job.create_gate_pass(self.clerk)

    def test_job_with_unsubmitted_inspection_is_blocked(self):
        self.start()
        self.assertIn("not submitted", self.job.shipping_block_reason())

    def test_job_pending_review_is_blocked_and_says_why(self):
        self.submitted_inspection()
        self.assertIn("waiting for a supervisor", self.job.shipping_block_reason())
        self.assertEqual(GatePass.objects.count(), 0)

    def test_approved_job_gets_a_gate_pass(self):
        self.submitted_inspection().approve(self.supervisor)
        gate_pass = self.job.create_gate_pass(self.clerk)
        self.assertEqual(gate_pass.created_by, self.clerk)

    def test_concession_job_gets_a_gate_pass(self):
        inspection = self.submitted_inspection(length="101.0")
        inspection.concede(self.supervisor, "customer_ok", "Customer accepted this length")
        self.assertIsNone(self.job.shipping_block_reason())

    def test_quarantined_job_is_blocked(self):
        self.submitted_inspection(diameter="25.500").quarantine(self.supervisor, "CTQ failed")
        self.assertIn("quarantined", self.job.shipping_block_reason())

    def test_quarantine_blocks_shipping_even_if_an_older_inspection_was_approved(self):
        first = self.submitted_inspection()
        first.approve(self.supervisor)
        second = self.submitted_inspection(diameter="25.500")
        second.quarantine(self.supervisor, "Found a CTQ problem on re-check")
        self.assertIn("quarantined", self.job.shipping_block_reason())

    def test_quarantine_blocks_shipping_even_if_a_later_inspection_looks_approved(self):
        self.submitted_inspection(diameter="25.500").quarantine(self.supervisor, "CTQ failed")
        # Create an approved inspection directly, skipping the rules on purpose.
        Inspection.objects.create(job=self.job, inspector=self.inspector, status=Inspection.APPROVED)
        self.assertIn("quarantined", self.job.shipping_block_reason())

    def test_gate_pass_can_only_be_created_once(self):
        self.submitted_inspection().approve(self.supervisor)
        self.job.create_gate_pass(self.clerk)
        with self.assertRaisesMessage(RuleError, "already been created"):
            self.job.create_gate_pass(self.clerk)


# ---------------------------------------------------------------------------
# Pages: login, roles and the whole flow through the browser
# ---------------------------------------------------------------------------

class RolePermissionTests(QualityTestCase):
    def login(self, user):
        self.client.login(username=user.username, password=self.PASSWORD)

    def test_anonymous_user_is_sent_to_login(self):
        response = self.client.get(reverse("job_list"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('job_list')}")

    def test_inspector_cannot_open_the_review_queue(self):
        self.login(self.inspector)
        self.assertEqual(self.client.get(reverse("review_queue")).status_code, 403)

    def test_inspector_cannot_open_shipping(self):
        self.login(self.inspector)
        self.assertEqual(self.client.get(reverse("shipping_list")).status_code, 403)

    def test_supervisor_cannot_open_the_jobs_page(self):
        self.login(self.supervisor)
        self.assertEqual(self.client.get(reverse("job_list")).status_code, 403)

    def test_clerk_cannot_review_inspections(self):
        inspection = self.submitted_inspection()
        self.login(self.clerk)
        self.assertEqual(self.client.get(reverse("review_detail", args=[inspection.pk])).status_code, 403)
        response = self.client.post(reverse("review_detail", args=[inspection.pk]), {"decision": "approve"})
        self.assertEqual(response.status_code, 403)

    def test_inspector_cannot_open_someone_elses_inspection(self):
        inspection = self.start()
        self.login(self.other_inspector)
        self.assertEqual(self.client.get(reverse("inspection_detail", args=[inspection.pk])).status_code, 403)

    def test_starting_an_inspection_needs_a_post(self):
        self.login(self.inspector)
        self.assertEqual(self.client.get(reverse("start_inspection", args=[self.job.pk])).status_code, 405)


class WholeFlowTests(QualityTestCase):
    def login(self, user):
        self.client.logout()
        self.client.login(username=user.username, password=self.PASSWORD)

    def post_measurement(self, inspection, field, value, gauge=None, photos=0):
        data = {"field_id": field.pk, "value": value, "gauge": (gauge or self.good_gauge).pk}
        if photos >= 1:
            data["photo1"] = make_photo("a.png")
        if photos >= 2:
            data["photo2"] = make_photo("b.png")
        return self.client.post(reverse("add_measurement", args=[inspection.pk]), data, follow=True)

    def test_inspector_to_supervisor_to_clerk(self):
        # Inspector starts, measures both fields and submits.
        self.login(self.inspector)
        self.client.post(reverse("start_inspection", args=[self.job.pk]))
        inspection = Inspection.objects.get()
        self.post_measurement(inspection, self.diameter, "25.010")
        self.post_measurement(inspection, self.length, "100.2")
        self.client.post(reverse("submit_inspection", args=[inspection.pk]))
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, Inspection.PENDING_REVIEW)

        # Supervisor approves.
        self.login(self.supervisor)
        self.assertContains(self.client.get(reverse("review_queue")), self.job.job_number)
        self.client.post(reverse("review_detail", args=[inspection.pk]), {"decision": "approve"})
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, Inspection.APPROVED)

        # Clerk creates the gate pass and can open it.
        self.login(self.clerk)
        self.client.post(reverse("create_gate_pass", args=[self.job.pk]))
        gate_pass = GatePass.objects.get()
        self.assertEqual(self.client.get(reverse("gate_pass_detail", args=[gate_pass.pk])).status_code, 200)

    def test_expired_gauge_message_is_shown_to_the_inspector(self):
        self.login(self.inspector)
        inspection = self.start()
        response = self.post_measurement(inspection, self.diameter, "25.0", gauge=self.expired_gauge)
        self.assertContains(response, "Expired Tool Calibration")
        self.assertEqual(inspection.measurements.count(), 0)

    def test_red_value_without_photos_shows_a_message_and_saves_nothing(self):
        self.login(self.inspector)
        inspection = self.start()
        response = self.post_measurement(inspection, self.length, "105")
        self.assertContains(response, "at least 2 photos")
        self.assertEqual(inspection.measurements.count(), 0)

    def test_red_value_with_two_photos_is_saved(self):
        self.login(self.inspector)
        inspection = self.start()
        self.post_measurement(inspection, self.length, "105", photos=2)
        self.assertEqual(inspection.measurements.get().photos.count(), 2)

    def test_supervisor_cannot_approve_a_failed_inspection_through_the_page(self):
        inspection = self.submitted_inspection(length="105")
        self.login(self.supervisor)
        response = self.client.post(
            reverse("review_detail", args=[inspection.pk]), {"decision": "approve"})
        self.assertContains(response, "cannot approve")
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, Inspection.PENDING_REVIEW)

    def test_ctq_concession_is_refused_even_if_the_request_is_sent_directly(self):
        inspection = self.submitted_inspection(diameter="25.500")
        self.login(self.supervisor)
        response = self.client.post(reverse("review_detail", args=[inspection.pk]), {
            "decision": "concession", "concession_code": "cosmetic",
            "note": "Please let this one through",
        })
        self.assertContains(response, "not allowed when a CTQ")
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, Inspection.PENDING_REVIEW)

    def test_clerk_sees_the_exact_reason_when_shipping_is_blocked(self):
        self.submitted_inspection()  # still pending review
        self.login(self.clerk)
        response = self.client.post(reverse("create_gate_pass", args=[self.job.pk]), follow=True)
        self.assertContains(response, "SHIPPING BLOCKED")
        self.assertContains(response, "waiting for a supervisor")
        self.assertEqual(GatePass.objects.count(), 0)
