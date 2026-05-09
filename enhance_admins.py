import os
import ast
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'texagonbackend.settings')
django.setup()

from django.apps import apps
from django.db import models
from django.contrib.admin import site

def get_searchable_fk_fields(model):
    fk_fields = []
    search_extensions = []
    
    for field in model._meta.fields:
        if isinstance(field, models.ForeignKey):
            related_model = field.related_model
            try:
                related_admin = site._registry.get(related_model)
                if related_admin and getattr(related_admin, 'search_fields', None):
                    fk_fields.append(field.name)
            except Exception:
                pass
                
            if field.name in ('user', 'student', 'teacher', 'organization'):
                if field.name == 'user':
                    search_extensions.extend([f"'{field.name}__email'", f"'{field.name}__first_name'", f"'{field.name}__last_name'"])
                elif field.name in ('student', 'teacher'):
                    search_extensions.extend([f"'{field.name}__user__email'", f"'{field.name}__user__first_name'", f"'{field.name}__user__last_name'"])
                elif field.name == 'organization':
                    search_extensions.extend([f"'{field.name}__name'"])
                    
    return fk_fields, search_extensions

def update_admin_file(filepath, app_config):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
        
    lines = content.split('\n')
    tree = ast.parse(content)
    
    modifications = []
    
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            is_model_admin = any(
                (isinstance(base, ast.Attribute) and base.attr == 'ModelAdmin') or 
                (isinstance(base, ast.Name) and base.id == 'ModelAdmin') or
                (isinstance(base, ast.Name) and base.id == 'UserAdmin')
                for base in node.bases
            )
            if not is_model_admin:
                continue
                
            model_name = node.name.replace('Admin', '')
            if model_name == 'User':
                model_name = 'User'
            
            try:
                model = apps.get_model(app_config.label, model_name)
            except LookupError:
                model = None
                for m in app_config.get_models():
                    if m.__name__ == model_name:
                        model = m
                        break
                if not model: continue
            
            fk_fields, search_extensions = get_searchable_fk_fields(model)
            
            has_autocomplete = any(isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'autocomplete_fields' for t in stmt.targets) for stmt in node.body)
            has_search_fields = any(isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'search_fields' for t in stmt.targets) for stmt in node.body)
            has_raw_id = any(isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'raw_id_fields' for t in stmt.targets) for stmt in node.body)
            
            insertions = []
            
            if fk_fields and not has_autocomplete and not has_raw_id:
                formatted_fks = ", ".join(f"'{f}'" for f in fk_fields)
                insertions.append(f"    autocomplete_fields = [{formatted_fks}]")
                
            if search_extensions and not has_search_fields:
                formatted_search = ", ".join(search_extensions)
                insertions.append(f"    search_fields = [{formatted_search}]")
                
            if insertions:
                # Insert directly after the class definition line (node.lineno)
                modifications.append((node.lineno, "\n".join(insertions)))
                
    modifications.sort(key=lambda x: x[0], reverse=True)
    
    new_lines = lines[:]
    for lineno, text in modifications:
        new_lines.insert(lineno, text)
        
    if modifications:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("\n".join(new_lines))
        print(f"Updated {filepath}")

def main():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    for app_config in apps.get_app_configs():
        app_path = app_config.path
        if not app_path.startswith(project_dir):
            continue
            
        admin_file = os.path.join(app_path, 'admin.py')
        if os.path.exists(admin_file):
            try:
                update_admin_file(admin_file, app_config)
            except Exception as e:
                print(f"Failed on {admin_file}: {e}")

if __name__ == '__main__':
    main()
