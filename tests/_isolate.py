"""Point AIRBANK_HOME at a throwaway dir BEFORE any airbank import so the
suite never reads or writes the user's real ~/.airbank. Import this first
in every test module."""
import os
import tempfile

if not os.environ.get("AIRBANK_HOME", "").startswith(tempfile.gettempdir()):
    os.environ["AIRBANK_HOME"] = tempfile.mkdtemp(prefix="airbank-test-")
os.environ.pop("AIRBANK_MODE", None)
