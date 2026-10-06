from decimal import Decimal
from datetime import date
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import RestrictedError
from django.db import IntegrityError

from core.models import Category, Budget, Expense
from core.views import calculate_budget_status


class BudgetCalculationAndThresholdLogicTests(TestCase):
    """
    Tests covering budget calculation, remaining amount, and alert threshold logic:
    - < 80%: Normal state (Green/Success)
    - 80% - 99.9%: Warning state (Yellow/Warning)
    - >= 100%: Danger state (Red/Danger)
    """

    def test_threshold_below_80_percent_is_normal(self):
        # 50 spent on 100 limit = 50.0% -> Normal
        result = calculate_budget_status(limit=Decimal('100.00'), spent=Decimal('50.00'))
        self.assertEqual(result['status'], 'normal')
        self.assertEqual(result['status_class'], 'success')
        self.assertEqual(result['badge_class'], 'bg-success')
        self.assertEqual(result['remaining'], Decimal('50.00'))
        self.assertEqual(result['percentage'], Decimal('50.00'))

    def test_threshold_boundary_just_below_80_percent(self):
        # 79.99 spent on 100 limit = 79.99% -> Normal (< 80%)
        result = calculate_budget_status(limit=Decimal('100.00'), spent=Decimal('79.99'))
        self.assertEqual(result['status'], 'normal')
        self.assertEqual(result['status_class'], 'success')
        self.assertEqual(result['badge_class'], 'bg-success')

    def test_threshold_exact_80_percent_is_warning(self):
        # 80.00 spent on 100 limit = 80.0% -> Warning
        result = calculate_budget_status(limit=Decimal('100.00'), spent=Decimal('80.00'))
        self.assertEqual(result['status'], 'warning')
        self.assertEqual(result['status_class'], 'warning')
        self.assertIn('bg-warning', result['badge_class'])
        self.assertEqual(result['remaining'], Decimal('20.00'))
        self.assertEqual(result['percentage'], Decimal('80.00'))

    def test_threshold_boundary_just_below_100_percent(self):
        # 99.99 spent on 100 limit = 99.99% -> Warning (80% - 99.9%)
        result = calculate_budget_status(limit=Decimal('100.00'), spent=Decimal('99.99'))
        self.assertEqual(result['status'], 'warning')
        self.assertEqual(result['status_class'], 'warning')
        self.assertIn('bg-warning', result['badge_class'])

    def test_threshold_exact_100_percent_is_danger(self):
        # 100.00 spent on 100 limit = 100.0% -> Danger
        result = calculate_budget_status(limit=Decimal('100.00'), spent=Decimal('100.00'))
        self.assertEqual(result['status'], 'danger')
        self.assertEqual(result['status_class'], 'danger')
        self.assertEqual(result['badge_class'], 'bg-danger')
        self.assertEqual(result['remaining'], Decimal('0.00'))
        self.assertEqual(result['percentage'], Decimal('100.00'))

    def test_threshold_exceeded_over_100_percent_is_danger(self):
        # 150.00 spent on 100 limit = 150.0% -> Danger (Remaining is negative)
        result = calculate_budget_status(limit=Decimal('100.00'), spent=Decimal('150.00'))
        self.assertEqual(result['status'], 'danger')
        self.assertEqual(result['status_class'], 'danger')
        self.assertEqual(result['badge_class'], 'bg-danger')
        self.assertEqual(result['remaining'], Decimal('-50.00'))
        self.assertEqual(result['percentage'], Decimal('150.00'))

    def test_zero_spending_is_normal(self):
        result = calculate_budget_status(limit=Decimal('200.00'), spent=Decimal('0.00'))
        self.assertEqual(result['status'], 'normal')
        self.assertEqual(result['remaining'], Decimal('200.00'))
        self.assertEqual(result['percentage'], Decimal('0.00'))


class ExpenseTrackerIntegrationTests(TestCase):
    """
    Tests covering user isolation, CRUD operations, Expense API validation,
    and category deletion restriction with active expenses.
    """

    def setUp(self):
        self.user1 = User.objects.create_user(username='alice', password='password123')
        self.user2 = User.objects.create_user(username='bob', password='password123')

        self.client1 = Client()
        self.client1.login(username='alice', password='password123')

        self.client2 = Client()
        self.client2.login(username='bob', password='password123')

        # Alice's category and budget
        self.cat_food = Category.objects.create(user=self.user1, name='Food', description='Groceries and dining')
        self.budget_food = Budget.objects.create(
            user=self.user1,
            category=self.cat_food,
            monthly_limit=Decimal('500.00'),
            month_year=date(2026, 10, 1)
        )

        # Bob's category
        self.cat_bob = Category.objects.create(user=self.user2, name='Travel', description='Flight tickets')

    def test_authentication_required_for_protected_views(self):
        anon_client = Client()
        protected_urls = [
            reverse('dashboard'),
            reverse('category_list'),
            reverse('category_create'),
            reverse('budget_list'),
            reverse('budget_create'),
            reverse('expense_create'),
        ]
        for url in protected_urls:
            response = anon_client.get(url)
            self.assertEqual(response.status_code, 302, f"URL {url} should redirect unauthenticated user")
            self.assertIn(reverse('login'), response.url)

    def test_expense_create_api_success(self):
        """
        Expense API: POST /expenses/create/
        Accepts form data (amount, date, category, notes),
        returns HTTP 302 redirect to dashboard on success.
        """
        payload = {
            'category': self.cat_food.pk,
            'amount': '120.50',
            'date': '2026-10-05',
            'notes': 'Weekly supermarket grocery run',
        }
        response = self.client1.post(reverse('expense_create'), payload)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('dashboard'))

        # Verify expense created and linked to user1
        expense = Expense.objects.get(notes='Weekly supermarket grocery run')
        self.assertEqual(expense.user, self.user1)
        self.assertEqual(expense.amount, Decimal('120.50'))
        self.assertEqual(expense.category, self.cat_food)

    def test_expense_create_api_rejects_negative_and_zero_amount(self):
        """
        Expense API: Rejects negative amounts, returns HTTP 200 with validation errors.
        """
        # Negative amount
        payload_neg = {
            'category': self.cat_food.pk,
            'amount': '-50.00',
            'date': '2026-10-05',
            'notes': 'Invalid negative amount',
        }
        response_neg = self.client1.post(reverse('expense_create'), payload_neg)
        self.assertEqual(response_neg.status_code, 200)
        self.assertFalse(Expense.objects.filter(notes='Invalid negative amount').exists())

        # Zero amount
        payload_zero = {
            'category': self.cat_food.pk,
            'amount': '0.00',
            'date': '2026-10-05',
            'notes': 'Invalid zero amount',
        }
        response_zero = self.client1.post(reverse('expense_create'), payload_zero)
        self.assertEqual(response_zero.status_code, 200)
        self.assertFalse(Expense.objects.filter(notes='Invalid zero amount').exists())

    def test_user_cannot_create_expense_with_another_users_category(self):
        """
        Security: User1 cannot use Bob's category for expense creation.
        Returns HTTP 200 with validation error.
        """
        payload = {
            'category': self.cat_bob.pk,
            'amount': '45.00',
            'date': '2026-10-05',
            'notes': 'Attempting IDOR',
        }
        response = self.client1.post(reverse('expense_create'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Expense.objects.filter(notes='Attempting IDOR').exists())

    def test_user_data_isolation(self):
        """
        Users must ONLY view/edit/delete their own data.
        """
        # User2 cannot view or edit User1's category
        response_edit = self.client2.get(reverse('category_update', kwargs={'pk': self.cat_food.pk}))
        self.assertEqual(response_edit.status_code, 404)

        # User2 cannot edit User1's budget
        response_budget_edit = self.client2.get(reverse('budget_update', kwargs={'pk': self.budget_food.pk}))
        self.assertEqual(response_budget_edit.status_code, 404)

        # User2 dashboard does not display User1's category or budget
        response_dash = self.client2.get(reverse('dashboard') + '?month=2026-10')
        self.assertEqual(response_dash.status_code, 200)
        self.assertNotContains(response_dash, 'Food')

    def test_prevent_deleting_category_with_active_expenses(self):
        """
        Edge Case: Prevent deleting a category if it has active expenses.
        Verified via model RestrictedError and view prevention.
        """
        Expense.objects.create(
            user=self.user1,
            category=self.cat_food,
            amount=Decimal('85.00'),
            date=date(2026, 10, 2)
        )

        # Attempt to delete via database directly should raise RestrictedError
        with self.assertRaises(RestrictedError):
            self.cat_food.delete()

        # Attempt to delete via view POST
        response = self.client1.post(reverse('category_delete', kwargs={'pk': self.cat_food.pk}))
        self.assertEqual(response.status_code, 302)

        # Category must still exist
        self.assertTrue(Category.objects.filter(pk=self.cat_food.pk).exists())

    def test_delete_category_without_expenses_succeeds(self):
        empty_cat = Category.objects.create(user=self.user1, name='Books')
        response = self.client1.post(reverse('category_delete', kwargs={'pk': empty_cat.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Category.objects.filter(pk=empty_cat.pk).exists())

    def test_dashboard_monthly_spending_and_category_alerts(self):
        """
        Verify dashboard calculates total spending and displays correct alert states.
        """
        # 1. Spend $200 of $500 -> 40% (Normal: Green)
        Expense.objects.create(
            user=self.user1,
            category=self.cat_food,
            amount=Decimal('200.00'),
            date=date(2026, 10, 3)
        )
        response = self.client1.get(reverse('dashboard') + '?month=2026-10')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_spent'], Decimal('200.00'))
        self.assertEqual(response.context['total_budget'], Decimal('500.00'))
        self.assertEqual(response.context['total_remaining'], Decimal('300.00'))

        summary = response.context['category_summaries'][0]
        self.assertEqual(summary['status'], 'normal')
        self.assertEqual(summary['remaining'], Decimal('300.00'))

        # 2. Add $250 more -> Total $450 of $500 -> 90% (Warning: Yellow)
        Expense.objects.create(
            user=self.user1,
            category=self.cat_food,
            amount=Decimal('250.00'),
            date=date(2026, 10, 4)
        )
        response = self.client1.get(reverse('dashboard') + '?month=2026-10')
        summary = response.context['category_summaries'][0]
        self.assertEqual(summary['status'], 'warning')
        self.assertEqual(summary['spent'], Decimal('450.00'))
        self.assertEqual(summary['remaining'], Decimal('50.00'))

        # 3. Add $100 more -> Total $550 of $500 -> 110% (Danger: Red)
        Expense.objects.create(
            user=self.user1,
            category=self.cat_food,
            amount=Decimal('100.00'),
            date=date(2026, 10, 5)
        )
        response = self.client1.get(reverse('dashboard') + '?month=2026-10')
        summary = response.context['category_summaries'][0]
        self.assertEqual(summary['status'], 'danger')
        self.assertEqual(summary['spent'], Decimal('550.00'))
        self.assertEqual(summary['remaining'], Decimal('-50.00'))

    def test_budget_unique_together_constraint(self):
        """
        Verify unique together constraint: user, category, month_year
        """
        with self.assertRaises(IntegrityError):
            Budget.objects.create(
                user=self.user1,
                category=self.cat_food,
                monthly_limit=Decimal('300.00'),
                month_year=date(2026, 10, 1)
            )
