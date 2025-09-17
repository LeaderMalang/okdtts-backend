from django.db import models

class OpeningBalanceTool(models.Model):
    class Meta:
        managed = False               # no DB table
        app_label = "finance"         # shows under the Finance app in admin
        verbose_name = "Opening Balance"
        verbose_name_plural = "Opening Balance"
