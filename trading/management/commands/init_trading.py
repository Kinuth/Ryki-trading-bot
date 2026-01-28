"""
Management command to initialize the trading system.
"""
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone

from trading.models import RiskState
from trading.services.redis_cache import RedisCache
from trading.services.binance_client import BinanceClient


class Command(BaseCommand):
    help = 'Initialize the trading system with starting balance and risk state'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Reset today\'s risk state',
        )

    def handle(self, *args, **options):
        self.stdout.write('Initializing trading system...')
        
        # Test Redis connection
        try:
            cache = RedisCache()
            if cache.health_check():
                self.stdout.write(self.style.SUCCESS('✓ Redis connection OK'))
            else:
                self.stdout.write(self.style.ERROR('✗ Redis connection failed'))
                return
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Redis error: {e}'))
            return
        
        # Test Binance connection
        try:
            client = BinanceClient()
            balance = client.get_account_balance('USDT')
            self.stdout.write(self.style.SUCCESS(f'✓ Binance connection OK (Balance: {balance} USDT)'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Binance error: {e}'))
            balance = Decimal('0')
        
        # Initialize or reset risk state
        if options['reset']:
            RiskState.objects.filter(date=timezone.now().date()).delete()
            self.stdout.write('Reset today\'s risk state')
        
        risk_state = RiskState.get_or_create_today(starting_balance=balance)
        self.stdout.write(self.style.SUCCESS(f'✓ Risk state initialized: {risk_state.system_status}'))
        
        # Set system status to active
        cache.set_system_status('ACTIVE', '')
        self.stdout.write(self.style.SUCCESS('✓ System status set to ACTIVE'))
        
        self.stdout.write(self.style.SUCCESS('\nTrading system initialized successfully!'))
        self.stdout.write(f'Daily starting balance: {risk_state.starting_balance} USDT')
