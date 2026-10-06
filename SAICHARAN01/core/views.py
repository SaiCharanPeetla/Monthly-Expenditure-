from decimal import Decimal
from datetime import datetime, date
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, RestrictedError
from django.utils import timezone
from django.http import JsonResponse, HttpResponseBadRequest

from .models import Category, Budget, Expense
from .forms import RegistrationForm, CategoryForm, BudgetForm, ExpenseForm


def calculate_budget_status(limit, spent):
    """
    Computes remaining budget, percentage, and alert threshold status.
    - < 80%: Normal state (Green/Success)
    - 80% - 99.9%: Warning state (Yellow/Warning)
    - >= 100%: Danger state (Red/Danger)
    """
    if limit is None:
        limit = Decimal('0.00')
    if spent is None:
        spent = Decimal('0.00')

    remaining = limit - spent
    percentage = (spent / limit * Decimal('100.0')) if limit > Decimal('0.00') else Decimal('0.00')

    if percentage < Decimal('80.0'):
        status = 'normal'
        status_class = 'success'
        badge_class = 'bg-success'
        label = 'Normal (< 80%)'
    elif percentage < Decimal('100.0'):
        status = 'warning'
        status_class = 'warning'
        badge_class = 'bg-warning text-dark'
        label = 'Warning (80% - 99.9%)'
    else:
        status = 'danger'
        status_class = 'danger'
        badge_class = 'bg-danger'
        label = 'Danger (>= 100%)'

    return {
        'limit': limit,
        'spent': spent,
        'remaining': remaining,
        'percentage': round(percentage, 2),
        'status': status,
        'status_class': status_class,
        'badge_class': badge_class,
        'label': label,
    }


def get_dashboard_context(user, target_date=None, expense_form=None):
    if target_date is None:
        target_date = timezone.now().date().replace(day=1)
    else:
        target_date = target_date.replace(day=1)

    # 1. Total monthly spending
    monthly_expenses = Expense.objects.filter(
        user=user,
        date__year=target_date.year,
        date__month=target_date.month
    ).select_related('category').order_by('-date', '-id')

    total_spent = monthly_expenses.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    # 2. Budgets for selected month
    monthly_budgets = Budget.objects.filter(
        user=user,
        month_year__year=target_date.year,
        month_year__month=target_date.month
    ).select_related('category')

    total_budget = monthly_budgets.aggregate(total=Sum('monthly_limit'))['total'] or Decimal('0.00')
    total_remaining = total_budget - total_spent

    # Overall budget threshold status
    overall_status = calculate_budget_status(total_budget, total_spent)

    # 3. Remaining budget per category & visual alerts
    category_summaries = []
    budgeted_category_ids = set()

    for b in monthly_budgets:
        budgeted_category_ids.add(b.category_id)
        cat_spent = monthly_expenses.filter(category=b.category).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        calc = calculate_budget_status(b.monthly_limit, cat_spent)
        category_summaries.append({
            'budget': b,
            'category': b.category,
            'limit': calc['limit'],
            'spent': calc['spent'],
            'remaining': calc['remaining'],
            'percentage': calc['percentage'],
            'status': calc['status'],
            'status_class': calc['status_class'],
            'badge_class': calc['badge_class'],
            'label': calc['label'],
        })

    # Unbudgeted categories that had spending this month
    unbudgeted_expenses = monthly_expenses.exclude(category_id__in=budgeted_category_ids)
    unbudgeted_categories = []
    seen_cats = set()
    for exp in unbudgeted_expenses:
        if exp.category_id not in seen_cats:
            seen_cats.add(exp.category_id)
            cat_spent = monthly_expenses.filter(category=exp.category).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            unbudgeted_categories.append({
                'category': exp.category,
                'spent': cat_spent,
            })

    if expense_form is None:
        expense_form = ExpenseForm(user=user, initial={'date': timezone.now().date()})

    user_categories = Category.objects.filter(user=user)

    return {
        'target_date': target_date,
        'current_month_str': target_date.strftime('%Y-%m'),
        'current_month_display': target_date.strftime('%B %Y'),
        'total_spent': total_spent,
        'total_budget': total_budget,
        'total_remaining': total_remaining,
        'overall_status': overall_status,
        'category_summaries': category_summaries,
        'unbudgeted_categories': unbudgeted_categories,
        'recent_expenses': monthly_expenses[:10],
        'expense_form': expense_form,
        'has_categories': user_categories.exists(),
        'user_categories': user_categories,
    }


# ============================================================================
# Authentication Views
# ============================================================================

def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            auth_login(request, user)
            messages.success(request, f"Welcome to Expense Tracker, {user.username}!")
            return redirect('dashboard')
    else:
        form = RegistrationForm()
    return render(request, 'core/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            auth_login(request, user)
            messages.success(request, f"Welcome back, {user.username}!")
            next_url = request.GET.get('next') or request.POST.get('next') or 'dashboard'
            return redirect(next_url)
    else:
        form = AuthenticationForm()
    return render(request, 'core/login.html', {'form': form})


def logout_view(request):
    auth_logout(request)
    messages.info(request, "You have been logged out.")
    return redirect('login')


# ============================================================================
# Dashboard View
# ============================================================================

@login_required
def dashboard_view(request):
    month_param = request.GET.get('month')
    target_date = None
    if month_param:
        try:
            target_date = datetime.strptime(month_param, '%Y-%m').date()
        except ValueError:
            target_date = timezone.now().date().replace(day=1)
    else:
        target_date = timezone.now().date().replace(day=1)

    context = get_dashboard_context(request.user, target_date=target_date)
    return render(request, 'core/dashboard.html', context)


# ============================================================================
# Expense API & Views
# ============================================================================

@login_required
def expense_create_view(request):
    """
    Expense API: POST /expenses/create/
    Accepts form data (amount, date, category, notes), rejects negative amounts,
    returns HTTP 302 redirect to dashboard on success, or HTTP 200 with validation errors.
    """
    if request.method != 'POST':
        return redirect('dashboard')

    form = ExpenseForm(request.POST, user=request.user)
    if form.is_valid():
        expense = form.save(commit=False)
        expense.user = request.user
        expense.save()
        messages.success(request, f"Expense of ${expense.amount} recorded for {expense.category.name}!")
        return redirect('dashboard')
    else:
        # Invalid form -> Return HTTP 200 with validation errors
        date_str = request.POST.get('date')
        target_date = timezone.now().date().replace(day=1)
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date().replace(day=1)
            except ValueError:
                pass

        context = get_dashboard_context(request.user, target_date=target_date, expense_form=form)
        return render(request, 'core/dashboard.html', context, status=200)


@login_required
def expense_delete_view(request, pk):
    expense = get_object_or_404(Expense, pk=pk, user=request.user)
    if request.method == 'POST':
        category_name = expense.category.name
        amount = expense.amount
        expense.delete()
        messages.success(request, f"Removed expense of ${amount} for {category_name}.")
    return redirect('dashboard')


# ============================================================================
# Category CRUD Views
# ============================================================================

@login_required
def category_list_view(request):
    categories = Category.objects.filter(user=request.user).order_by('name')
    return render(request, 'core/categories.html', {'categories': categories})


@login_required
def category_create_view(request):
    if request.method == 'POST':
        form = CategoryForm(request.POST)
        if form.is_valid():
            category = form.save(commit=False)
            category.user = request.user
            category.save()
            messages.success(request, f"Category '{category.name}' created successfully!")
            return redirect('category_list')
    else:
        form = CategoryForm()
    return render(request, 'core/categories_form.html', {'form': form, 'title': 'Create Category'})


@login_required
def category_update_view(request, pk):
    category = get_object_or_404(Category, pk=pk, user=request.user)
    if request.method == 'POST':
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, f"Category '{category.name}' updated successfully!")
            return redirect('category_list')
    else:
        form = CategoryForm(instance=category)
    return render(request, 'core/categories_form.html', {'form': form, 'category': category, 'title': 'Edit Category'})


@login_required
def category_delete_view(request, pk):
    category = get_object_or_404(Category, pk=pk, user=request.user)
    if request.method == 'POST':
        if category.expenses.exists():
            messages.error(request, f"Cannot delete category '{category.name}' because it has active expenses.")
            return redirect('category_list')
        try:
            category.delete()
            messages.success(request, f"Category '{category.name}' deleted successfully.")
        except RestrictedError:
            messages.error(request, f"Cannot delete category '{category.name}' because it has active expenses.")
        return redirect('category_list')
    return render(request, 'core/categories.html', {'category_to_delete': category, 'categories': Category.objects.filter(user=request.user)})


# ============================================================================
# Budget CRUD Views
# ============================================================================

@login_required
def budget_list_view(request):
    budgets = Budget.objects.filter(user=request.user).select_related('category').order_by('-month_year', 'category__name')
    return render(request, 'core/budgets.html', {'budgets': budgets})


@login_required
def budget_create_view(request):
    if request.method == 'POST':
        form = BudgetForm(request.POST, user=request.user)
        if form.is_valid():
            budget = form.save(commit=False)
            budget.user = request.user
            budget.save()
            messages.success(request, f"Budget for {budget.category.name} set to ${budget.monthly_limit}!")
            return redirect('budget_list')
    else:
        initial_date = timezone.now().date().replace(day=1)
        form = BudgetForm(user=request.user, initial={'month_year': initial_date})
    return render(request, 'core/budgets_form.html', {'form': form, 'title': 'Set New Budget'})


@login_required
def budget_update_view(request, pk):
    budget = get_object_or_404(Budget, pk=pk, user=request.user)
    if request.method == 'POST':
        form = BudgetForm(request.POST, instance=budget, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, f"Budget for {budget.category.name} updated successfully!")
            return redirect('budget_list')
    else:
        form = BudgetForm(instance=budget, user=request.user)
    return render(request, 'core/budgets_form.html', {'form': form, 'budget': budget, 'title': 'Edit Budget'})


@login_required
def budget_delete_view(request, pk):
    budget = get_object_or_404(Budget, pk=pk, user=request.user)
    if request.method == 'POST':
        category_name = budget.category.name
        budget.delete()
        messages.success(request, f"Budget for {category_name} removed.")
        return redirect('budget_list')
    return redirect('budget_list')
