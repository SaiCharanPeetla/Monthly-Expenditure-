from django.urls import path
from . import views

urlpatterns = [
    # Dashboard
    path('', views.dashboard_view, name='dashboard'),

    # Authentication
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    # Category CRUD
    path('categories/', views.category_list_view, name='category_list'),
    path('categories/create/', views.category_create_view, name='category_create'),
    path('categories/<int:pk>/edit/', views.category_update_view, name='category_update'),
    path('categories/<int:pk>/delete/', views.category_delete_view, name='category_delete'),

    # Budget CRUD
    path('budgets/', views.budget_list_view, name='budget_list'),
    path('budgets/create/', views.budget_create_view, name='budget_create'),
    path('budgets/<int:pk>/edit/', views.budget_update_view, name='budget_update'),
    path('budgets/<int:pk>/delete/', views.budget_delete_view, name='budget_delete'),

    # Expense API & Actions
    path('expenses/create/', views.expense_create_view, name='expense_create'),
    path('expenses/<int:pk>/delete/', views.expense_delete_view, name='expense_delete'),
]
