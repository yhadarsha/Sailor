from django.urls import path
from apps.imports import views

app_name = "imports"

urlpatterns = [
    path("imports/",                       views.wizard,        name="wizard"),
    path("imports/upload/",                views.upload,        name="upload"),
    path("imports/run/",                   views.run_import,    name="run"),
    path("imports/template/<uuid:template_id>/", views.load_template, name="load_template"),
]
