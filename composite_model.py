#!/usr/bin/env python3
"""
composite_model.py

Improved version: collects model classes from:
 - files named models.py
 - any file inside a folder named 'models' (models package)
 - files that contain classes which either subclass Model OR include assignments using models.<Field>()

Writes combined imports + class defs into all_model.py
"""

import ast
import os
from pathlib import Path
import argparse

SKIP_DIRS = {"migrations", "__pycache__", ".git", "venv", "env", ".venv", "node_modules"}


def get_full_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
    return None


def file_contains_models_like(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False
    return ("django" in text and "models" in text) or "Model" in text


def extract_from_file(path: Path):
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return [], []

    imports_src = []
    model_class_src = []

    # gather top-level imports
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            seg = ast.get_source_segment(src, node)
            if seg:
                imports_src.append(seg.rstrip())

    # find classes: expanded heuristic
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue

        is_model = False

        # 1) If file is models.py or inside a 'models' package, treat all classes there as candidates
        if path.name == "models.py" or "models" in path.parts:
            is_model = True
        else:
            # 2) Check bases for direct Model subclassing (models.Model or Model)
            for base in node.bases:
                full = get_full_name(base)
                if full is None:
                    full = ast.get_source_segment(src, base) or ""
                if full == "Model" or full.endswith(".Model"):
                    is_model = True
                    break

            # 3) Check class body for assignments that call models.<Field> (e.g. models.CharField(...))
            if not is_model:
                for stmt in node.body:
                    # only inspect attribute assignments (simple heuristic)
                    if isinstance(stmt, ast.Assign):
                        val = stmt.value
                        if isinstance(val, ast.Call):
                            func = val.func
                            fname = get_full_name(func)
                            if fname:
                                # common pattern: models.CharField / models.ForeignKey / CharField (if imported)
                                if "models." in fname or fname.endswith("Field") or "ForeignKey" in fname or "OneToOneField" in fname or "ManyToManyField" in fname:
                                    is_model = True
                                    break
                    # also check AnnAssign (PEP526 style)
                    if isinstance(stmt, ast.AnnAssign):
                        val = stmt.value
                        if isinstance(val, ast.Call):
                            fname = get_full_name(val.func)
                            if fname and ("models." in fname or fname.endswith("Field")):
                                is_model = True
                                break

        if is_model:
            seg = ast.get_source_segment(src, node)
            if seg:
                model_class_src.append(seg.rstrip())

    return imports_src, model_class_src


def find_model_files(project_root: Path):
    out = []
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            full = Path(dirpath) / fname

            # accept any models.py, or any file in a folder named 'models', or any file within an app folder
            if fname == "models.py" or "models" in Path(dirpath).parts:
                try:
                    if file_contains_models_like(full):
                        out.append(full)
                except Exception:
                    continue
            else:
                # also keep files that mention 'models' or 'django' or 'Model' (slightly broader)
                try:
                    if file_contains_models_like(full):
                        out.append(full)
                except Exception:
                    continue
    return sorted(set(out))


def unique_preserve_order(seq):
    seen = set()
    out = []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", "-p", default=".", help="Path to the Django project root")
    parser.add_argument("--output", "-o", default="all_model.py", help="Output file path")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    out_file = Path(args.output).resolve()

    files = find_model_files(project_root)
    if not files:
        print("No model files found under", project_root)
        return

    all_imports = []
    all_classes = []
    for f in files:
        try:
            imports_src, classes_src = extract_from_file(f)
        except Exception as e:
            print(f"Warning: failed to parse {f}: {e}")
            continue
        if classes_src:
            print(f"Found {len(classes_src)} model class(es) in {f}")
            all_imports.extend(imports_src)
            all_classes.extend(classes_src)

    if not all_classes:
        print("No model classes detected.")
        return

    all_imports = unique_preserve_order(all_imports)
    all_classes = unique_preserve_order(all_classes)

    # ensure django models import present
    has_django_models_import = any(
        ("django.db" in s and "models" in s) or ("from django.db import models" in s) or ("from django.db.models import" in s)
        for s in all_imports
    )
    if not has_django_models_import:
        all_imports.insert(0, "from django.db import models")

    header = (
        "# GENERATED by composite_model.py\n"
        "# Combines model classes from multiple apps into one file.\n"
        "# You may need to adjust imports / resolve name collisions manually.\n\n"
    )

    content_lines = [header]
    content_lines.append("# --- Imports collected from model files ---\n")
    for im in all_imports:
        content_lines.append(im)
    content_lines.append("\n\n# --- Model classes collected from project ---\n")
    for cls_src in all_classes:
        content_lines.append(cls_src)
        content_lines.append("\n\n")

    out_text = "\n".join(content_lines).rstrip() + "\n"
    out_file.write_text(out_text, encoding="utf-8")
    print(f"Wrote {len(all_classes)} classes and {len(all_imports)} import lines to {out_file}")


if __name__ == "__main__":
    main()
