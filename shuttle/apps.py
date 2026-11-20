from django.apps import AppConfig


class ShuttleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shuttle"

    def ready(self):
        from . import worker
        if worker.should_start():
            worker.start()