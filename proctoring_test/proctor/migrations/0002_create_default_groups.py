from django.db import migrations
from django.contrib.auth.models import Group

def create_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name='Students')
    Group.objects.get_or_create(name='Teachers')

class Migration(migrations.Migration):
    dependencies = [
        ('proctor', '0001_initial'),  # Replace with your latest migration
    ]
    operations = [
        migrations.RunPython(create_groups),
    ]