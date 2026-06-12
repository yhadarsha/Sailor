from django.urls import path
from apps.campaigns import views

app_name = "campaigns"

urlpatterns = [
    path("campaigns/",                                                        views.campaign_list,       name="campaign_list"),
    path("campaigns/new/",                                                    views.campaign_create,     name="campaign_create"),
    path("campaigns/<uuid:campaign_id>/",                                     views.campaign_detail,     name="campaign_detail"),
    path("campaigns/<uuid:campaign_id>/status/",                              views.campaign_set_status, name="campaign_set_status"),
    path("campaigns/<uuid:campaign_id>/steps/add/",                           views.campaign_add_step,   name="campaign_add_step"),
    path("campaigns/<uuid:campaign_id>/steps/<uuid:step_id>/delete/",         views.campaign_delete_step, name="campaign_delete_step"),
    path("campaigns/<uuid:campaign_id>/enroll/",                              views.campaign_enroll,     name="campaign_enroll"),
    path("campaigns/<uuid:campaign_id>/send-now/",                            views.campaign_send_now,   name="campaign_send_now"),
    path("campaigns/<uuid:campaign_id>/enrollments/<uuid:enrollment_id>/exit/",  views.campaign_exit_lead,      name="campaign_exit_lead"),
    path("campaigns/<uuid:campaign_id>/steps/<uuid:step_id>/mark-done/",        views.campaign_mark_step_done, name="campaign_mark_step_done"),
    path("campaigns/<uuid:campaign_id>/bulk-log/",                               views.campaign_bulk_log,    name="campaign_bulk_log"),
    # Public — no auth, hit by email clients
    path("campaigns/pixel/<uuid:send_id>/",                                   views.campaign_pixel,         name="campaign_pixel"),
    # Email templates
    path("campaigns/templates/",                                              views.email_template_list,    name="email_template_list"),
    path("campaigns/templates/<uuid:template_id>/delete/",                    views.email_template_delete,  name="email_template_delete"),
    path("campaigns/templates/json/",                                         views.email_templates_json,   name="email_templates_json"),
]
