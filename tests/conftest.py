import os
import sys
from pathlib import Path

# Force offline mode for tests, whatever is in .env (load_dotenv won't override these).
os.environ["WALLET_BACKEND"] = "mock"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["MOCK_TAP_DELAY"] = "0"
os.environ["FAKE_TAP_DELAY"] = "0"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
