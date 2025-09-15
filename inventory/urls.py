from django.urls import path
from . import views


app_name = "inventory" 
urlpatterns = [
    path('price-lists/', views.price_list_list, name='price_list_list'),
    path('price-lists/<int:pk>/', views.price_list_detail, name='price_list_detail'),
    path('levels/', views.inventory_levels, name='inventory_levels'),
    path('products/', views.product_list, name='product_list'),
    path('parties/', views.party_list, name='party_list'),
]
