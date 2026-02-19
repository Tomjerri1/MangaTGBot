"""
Налаштування шляхів для імпорту модулів.
Імпортується першим рядком в Main.py і bot.py.
"""
import sys
import os

# Додаємо корінь проекту в PYTHONPATH один раз
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))