import sys
import atexit
from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        # run_server.py calls migrate after django.setup() completes, so we don't
        # repeat it here — calling it inside ready() triggers a Django warning about
        # database access before the app registry is fully initialized.
        if not getattr(sys, 'frozen', False):
            return

        # Lazy: AppConfig.ready() is the earliest safe point to access the ORM and other
        # app-level state. Django explicitly requires that imports touching the app registry
        # or database models happen inside ready(), not at module load time. Moving any of
        # these to the top of apps.py would violate that contract and trigger Django warnings.
        from api.minimax import _restart_pool
        from api.evaluation import refresh_nn_weight
        from api import thermal_monitor
        from api.hardware import HW, flush_detection_log

        # Init thermal monitor first — minimax and views will call set_pool/notify later
        thermal_monitor.init(HW)

        atexit.register(_restart_pool)
        # Sync the dynamic NN blend ratio with how many games have been played so far
        refresh_nn_weight()
        # Log GPU detection result (only runs when hardware_config.json was freshly written)
        flush_detection_log()
