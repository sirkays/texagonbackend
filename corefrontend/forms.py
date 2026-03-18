from django.contrib.auth.forms import AuthenticationForm
from django import forms


class DashboardLoginForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={
            'class': 'field',
            'placeholder': 'email',
            'autocomplete': 'username',
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'field',
            'placeholder': 'Password',
            'autocomplete': 'current-password',
        })
    )