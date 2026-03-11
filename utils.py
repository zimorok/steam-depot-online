import sys
import asyncio

# PIL availability check
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    ImageTk = None
    Image = None

# New way: Define which loop factory to use based on the platform
# We only need SelectorEventLoop on Windows if we're using specific 
# legacy networking code that doesn't support the default Proactor loop.
LOOP_FACTORY = None
if sys.platform == "win32":
    LOOP_FACTORY = asyncio.SelectorEventLoop
