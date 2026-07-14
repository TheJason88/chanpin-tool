"""
No-op sitecustomize.

This file is intentionally kept empty because Python imports sitecustomize before
Streamlit starts app.py.  Do not install import hooks or business patches here;
all runtime business rules must be loaded explicitly from app.py via
`delivery_runtime.bootstrap(...)` so startup remains deterministic.
"""
