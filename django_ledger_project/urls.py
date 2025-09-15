"""
URL configuration for django_ledger_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)


urlpatterns = [
    path('admin/', admin.site.urls),
    # path('ledger/', include('django_ledger.urls', namespace='django_ledger')),
    path('', include('hordak.urls', namespace='hordak')),
    path('sales/', include('sale.urls')),  # 👈 Add this line

    path('ecommerce/', include('ecommerce.urls')),
    path('purchase/', include('purchase.urls')),
    path('finance/', include('finance.urls')),



    path('inventory/', include('inventory.urls')),
    path('voucher/', include('voucher.urls')),

    path('crm/', include('crm.urls')),
    path('tasks/', include('task.urls')),
    path('notifications/', include('notification.urls')),
    path('pricing/', include('pricing.urls')),
    path('expenses/', include('expense.urls')),
    path('investor/', include('investor.urls')),
    path('reports/', include('report.urls')),
    path('sync/', include('syncqueue.urls')),

    path('hr/', include('hr.urls')),
    path('user/', include('user.urls')),
    path('management/', include('setting.urls')),



    path('account/', include('hordak.urls', namespace='hordak')),
    path('spectacular/', SpectacularAPIView.as_view(), name='schema'),
    path('spectacular/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('spectacular/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]+ static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)


admin.site.site_header = "Okay Distribution"
admin.site.index_title = "Okay Distribution"
admin.site.site_title = "Okay Distribution"
