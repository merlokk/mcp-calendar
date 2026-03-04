import sys
import os

# Add the icscal package directory to sys.path so that
# absolute imports (calendar_loader, windows_zones) work
# when pytest is run from the project root.
sys.path.insert(0, os.path.dirname(__file__))
