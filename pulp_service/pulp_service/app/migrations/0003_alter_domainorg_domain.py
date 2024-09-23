# Generated by Django 4.2.10 on 2024-09-10 18:19

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('service', '0002_alter_domainorg_unique_together_domainorg_user_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='domainorg',
            name='domain',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='domains', to='core.domain', unique=True),
        ),
    ]