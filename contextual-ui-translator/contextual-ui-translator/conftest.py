"""Makes the project root importable when pytest is started from anywhere."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
