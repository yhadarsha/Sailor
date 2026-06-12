from django.urls import path
from apps.pipeline import views

app_name = "pipeline"

urlpatterns = [
    path("pipeline/",                            views.board,          name="board"),
    path("pipeline/move/<uuid:lead_id>/",        views.move_lead,      name="move_lead"),
    path("pipeline/add/",                        views.add_lead,       name="add_lead"),
    path("pipeline/dead/",                       views.dead_board,     name="dead_board"),
    path("dashboard/",                           views.dashboard,      name="dashboard"),
    path("leads/<uuid:lead_id>/",                views.lead_detail,    name="lead_detail"),
    path("leads/<uuid:lead_id>/edit/",           views.edit_lead,      name="edit_lead"),
    path("leads/<uuid:lead_id>/log-activity/",   views.log_activity,   name="log_activity"),
    path("leads/<uuid:lead_id>/delete/",         views.delete_lead,    name="delete_lead"),
    path("leads/<uuid:lead_id>/mark-dead/",      views.mark_dead,      name="mark_dead"),
    path("leads/<uuid:lead_id>/mark-bounced/",   views.mark_bounced,   name="mark_bounced"),
    path("leads/<uuid:lead_id>/mark-converted/", views.mark_converted, name="mark_converted"),
    path("leads/bulk-move/",                     views.bulk_move_leads,     name="bulk_move_leads"),
    path("leads/<uuid:lead_id>/send-email/",     views.send_email,          name="send_email"),
    # Public — no auth required; action_id acts as secret token
    path("email-pixel/<uuid:action_id>/",        views.email_pixel,         name="email_pixel"),
    path("email-reply/<uuid:action_id>/",        views.email_reply_incoming, name="email_reply_incoming"),
]
