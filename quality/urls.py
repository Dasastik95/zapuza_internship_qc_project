from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),

    # Inspector
    path("jobs/", views.job_list, name="job_list"),
    path("jobs/<int:job_id>/inspect/", views.start_inspection, name="start_inspection"),
    path("inspections/<int:pk>/", views.inspection_detail, name="inspection_detail"),
    path("inspections/<int:pk>/measure/", views.add_measurement, name="add_measurement"),
    path("inspections/<int:pk>/submit/", views.submit_inspection, name="submit_inspection"),

    # Supervisor
    path("review/", views.review_queue, name="review_queue"),
    path("review/<int:pk>/", views.review_detail, name="review_detail"),

    # Warehouse clerk
    path("shipping/", views.shipping_list, name="shipping_list"),
    path("shipping/<int:job_id>/gate-pass/", views.create_gate_pass, name="create_gate_pass"),
    path("gate-passes/<int:pk>/", views.gate_pass_detail, name="gate_pass_detail"),
]
