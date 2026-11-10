"""`python -m ahbo.evals` — same as `ahbo eval`."""
import sys

from .runner import main

sys.exit(main())
