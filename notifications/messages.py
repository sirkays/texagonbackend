# texagon_academy\texagonbackend\notifications\messages.py
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional
from django.template.loader import render_to_string
from django.utils.html import strip_tags

@dataclass(frozen=True)
class MessageSpec:
    """
    One reusable message definition that can produce:
    - Notification title/body/data (in-app)
    - Email subject/text/html
    """
    kind: str
    title_template: str                    # e.g. "Payment received"
    body_template: str                     # e.g. "Hi {{ user.first_name }}, ..."
    email_subject_template: Optional[str] = None
    email_html_template: Optional[str] = None   # template path e.g. "emails/payment_receipt.html"
    email_text_template: Optional[str] = None   # template path e.g. "emails/payment_receipt.txt"
    default_data: Dict[str, Any] = field(default_factory=dict)

    def render_title(self, ctx: Dict[str, Any]) -> str:
        # allows both plain strings and template strings
        return render_to_string(
            template_name=None,
            context=ctx,
            template_engine=None,
            using=None,
            dirs=None,
            string_if_invalid="",
        ) if False else _render_inline(self.title_template, ctx)

    def render_body(self, ctx: Dict[str, Any]) -> str:
        return _render_inline(self.body_template, ctx)

    def render_email_subject(self, ctx: Dict[str, Any]) -> Optional[str]:
        if not self.email_subject_template:
            return None
        return _render_inline(self.email_subject_template, ctx)

    def render_email(self, ctx: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        """
        Returns (text, html)
        """
        html = render_to_string(self.email_html_template, ctx) if self.email_html_template else None
        text = render_to_string(self.email_text_template, ctx) if self.email_text_template else None

        # If html exists but no text template, generate a fallback
        if html and not text:
            text = strip_tags(html)
        return text, html


def _render_inline(template_string: str, ctx: Dict[str, Any]) -> str:
    """
    Render a short Django template string (not a file).
    """
    from django.template import Context, Template
    return Template(template_string).render(Context(ctx)).strip()
