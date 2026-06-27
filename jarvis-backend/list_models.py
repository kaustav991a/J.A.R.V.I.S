import os
import sys
sys.path.insert(0, os.path.abspath('.'))

from modules.groq_key_manager import get_initial_client

client = get_initial_client()
models = client.models.list().data
print('\n'.join(m.id for m in models))
