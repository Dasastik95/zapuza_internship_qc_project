from django.contrib import admin

from .models import (
    DefectPhoto, GatePass, Gauge, Inspection, InspectionField, Job, Machine, Measurement, Product,
)


class InspectionFieldInline(admin.TabularInline):
    model = InspectionField
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("part_number", "name")
    search_fields = ("part_number", "name")
    inlines = [InspectionFieldInline]


@admin.register(Gauge)
class GaugeAdmin(admin.ModelAdmin):
    list_display = ("gauge_id", "name", "calibration_expiry", "expired")
    search_fields = ("gauge_id", "name")

    @admin.display(boolean=True, description="Expired?")
    def expired(self, gauge):
        return gauge.is_expired()


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("job_number", "product", "machine", "quantity", "created_at")
    list_filter = ("machine", "product")
    search_fields = ("job_number",)


admin.site.register(Machine)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Inspection results can only be changed through the app, so the rules cannot be bypassed."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Inspection)
class InspectionAdmin(ReadOnlyAdmin):
    list_display = ("id", "job", "inspector", "status", "reviewed_by", "created_at")
    list_filter = ("status",)


@admin.register(Measurement)
class MeasurementAdmin(ReadOnlyAdmin):
    list_display = ("inspection", "field", "value", "passed", "gauge")
    list_filter = ("passed",)


@admin.register(GatePass)
class GatePassAdmin(ReadOnlyAdmin):
    list_display = ("id", "job", "created_by", "created_at")


admin.site.register(DefectPhoto)
