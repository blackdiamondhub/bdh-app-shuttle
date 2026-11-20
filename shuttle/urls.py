from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="shuttle"),
    path("api/live/", views.live_view, name="shuttle_live"),
    path("api/reminders/", views.reminders_view, name="shuttle_reminders"),
    path("api/speakers/", views.speakers_view, name="shuttle_speakers"),
    path("popup/<int:minutes>/", views.popup, name="shuttle_popup"),
]
