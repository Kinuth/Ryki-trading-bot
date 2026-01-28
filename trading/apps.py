from django.apps import AppConfig


class TradingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'trading'
    verbose_name = 'Ryki Trading System'

    def ready(self):
        """Initialize trading system components when Django starts."""
        # Import signals if needed
        pass
