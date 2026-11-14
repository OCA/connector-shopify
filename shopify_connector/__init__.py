try:
    import odoo
except ModuleNotFoundError as exc:
    if exc.name != "odoo":
        raise
else:
    from . import controllers
    from . import models
    from . import wizards
