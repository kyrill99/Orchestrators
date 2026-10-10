import logging

# The tests trigger fallbacks on purpose; keep their warnings out of the output.
logging.getLogger("strategy_interview").setLevel(logging.CRITICAL)
