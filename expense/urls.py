from rest_framework.routers import DefaultRouter
from .views import ExpenseCategoryViewSet, ExpenseViewSet

router = DefaultRouter()
router.register(r'categories', ExpenseCategoryViewSet)
router.register(r'expenses', ExpenseViewSet)

urlpatterns = router.urls
