import os
import re
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'texagonbackend.settings')
django.setup()

from django.apps import apps
from django.db import models

def get_admin_code_for_model(model):
    model_name = model.__name__
    list_display = []
    list_filter = []
    search_fields = []
    
    for field in model._meta.fields:
        field_name = field.name
        if isinstance(field, (models.CharField, models.EmailField, models.SlugField, models.URLField)):
            search_fields.append(f"'{field_name}'")
            
        if isinstance(field, (models.BooleanField, models.DateField, models.DateTimeField)):
            list_filter.append(f"'{field_name}'")
        elif isinstance(field, models.ForeignKey):
            list_filter.append(f"'{field_name}'")
            
        if getattr(field, 'choices', None):
            if f"'{field_name}'" not in list_filter:
                list_filter.append(f"'{field_name}'")
                
        if not isinstance(field, models.TextField) and len(list_display) < 8:
            list_display.append(f"'{field_name}'")

    if not list_display:
        list_display = ["'id'"]

    lines = []
    lines.append(f"@admin.register({model_name})")
    lines.append(f"class {model_name}Admin(admin.ModelAdmin):")
    lines.append(f"    list_display = [{', '.join(list_display)}]")
    if list_filter:
        lines.append(f"    list_filter = [{', '.join(list_filter)}]")
    if search_fields:
        lines.append(f"    search_fields = [{', '.join(search_fields)}]")
    lines.append("")
    
    return "\n".join(lines)

def main():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    
    for app_config in apps.get_app_configs():
        app_path = app_config.path
        if not app_path.startswith(project_dir):
            continue
            
        admin_file = os.path.join(app_path, 'admin.py')
        if not os.path.exists(admin_file):
            continue
            
        with open(admin_file, 'r', encoding='utf-8') as f:
            content = f.read()

        added_models = []
        new_content = content

        for model in app_config.get_models():
            model_name = model.__name__
            if model_name == "User":
                continue

            has_custom_admin = re.search(fr"class\s+{model_name}Admin\b", new_content)
            if has_custom_admin:
                continue

            simple_register_pattern = fr"admin\.site\.register\(\s*{model_name}\s*\)"
            has_simple_register = re.search(simple_register_pattern, new_content)

            admin_code = get_admin_code_for_model(model)
            
            if has_simple_register:
                new_content = re.sub(simple_register_pattern, admin_code, new_content)
                added_models.append(model_name)
                
                # Import the model
                if f"import {model_name}" not in new_content and f"from .models import *" not in new_content:
                    lines = new_content.split('\n')
                    insert_idx = 0
                    for i, line in enumerate(lines):
                        if line.startswith('from .models import '):
                            lines[i] = line + f", {model_name}"
                            break
                    else:
                        new_content = f"from .models import {model_name}\n" + new_content

        if added_models:
            with open(admin_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Updated {admin_file} with admins for {', '.join(added_models)}")

if __name__ == '__main__':
    main()
