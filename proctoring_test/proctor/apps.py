# proctor/apps.py
from django.apps import AppConfig

class ProctorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'proctor'

    def ready(self):
        # Remove the Group creation from here
        pass