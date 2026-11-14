try:
    import odoo
except ModuleNotFoundError as exc:
    if exc.name != "odoo":
        raise
else:
    from . import models
