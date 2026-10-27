import os, sys, tempfile

# Isolate AppData before framekeep.paths is imported, so tests never touch the real config
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="framekeep-test-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
