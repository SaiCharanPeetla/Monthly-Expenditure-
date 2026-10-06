from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError


class Category(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name_plural = 'Categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class Budget(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='budgets')
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='budgets')
    monthly_limit = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'), message="Monthly limit must be greater than zero.")]
    )
    month_year = models.DateField(help_text="Month and year for the budget (stored as the 1st of the month)")

    class Meta:
        unique_together = ('user', 'category', 'month_year')
        ordering = ['-month_year', 'category__name']

    def clean(self):
        super().clean()
        if self.month_year:
            self.month_year = self.month_year.replace(day=1)
        if self.monthly_limit is not None and self.monthly_limit <= Decimal('0.00'):
            raise ValidationError({'monthly_limit': "Monthly limit must be greater than zero."})

    def save(self, *args, **kwargs):
        if self.month_year:
            self.month_year = self.month_year.replace(day=1)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.category.name} - {self.month_year.strftime('%b %Y')}: ${self.monthly_limit}"


class Expense(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='expenses')
    category = models.ForeignKey(Category, on_delete=models.RESTRICT, related_name='expenses')
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'), message="Amount must be greater than zero.")]
    )
    date = models.DateField()
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-date', '-id']

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount <= Decimal('0.00'):
            raise ValidationError({'amount': "Amount must be greater than zero."})

    def __str__(self):
        return f"{self.category.name} - ${self.amount} ({self.date})"
