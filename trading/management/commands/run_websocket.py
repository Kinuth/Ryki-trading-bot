"""
Management command to start the WebSocket manager.
"""
import asyncio
from django.core.management.base import BaseCommand

from trading.services.websocket_manager import start_websocket_manager, stop_websocket_manager


class Command(BaseCommand):
    help = 'Start the Binance WebSocket manager for real-time data streaming'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting WebSocket manager...'))
        
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(start_websocket_manager())
            
            # Keep running until interrupted
            loop.run_forever()
            
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('Shutting down...'))
            loop.run_until_complete(stop_websocket_manager())
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {e}'))
            raise
