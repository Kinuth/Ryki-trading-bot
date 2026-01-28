"""
WSGI config for ryki_trading project.
"""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ryki_trading.settings')

application = get_wsgi_application()
