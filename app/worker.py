import os
import time
from celery import Celery

# Docker lo Redis URL
CELERY_BROKER_URL = "redis://redis:6379/0"

# Celery App Config
celery = Celery(__name__, broker=CELERY_BROKER_URL, backend=CELERY_BROKER_URL)

@celery.task(name="send_order_email")
def send_order_email(email: str, order_id: int):
    # Simulate Email sending (Wait 5 seconds)
    print(f"⏳ Sending email to {email} for Order #{order_id}...")
    time.sleep(5)
    print(f"✅ EMAIL SENT to {email}: Order #{order_id} Confirmed!")
    return True