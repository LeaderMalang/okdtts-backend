from django.urls import path
from . import views

urlpatterns = [

    path('', views.report_dashboard, name='report_dashboard'),
    path('ratios/', views.financial_ratios, name='financial_ratios'),

 
    path("financial-statement/", views.financial_statement, name="financial_statement"),

]
