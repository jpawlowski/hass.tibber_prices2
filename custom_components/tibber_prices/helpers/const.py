"""Constants for the Tibber Prices helper modules."""

# Time constants
HOURS_IN_DAY = 24
SPRING_FORWARD_HOURS = 23
FALL_BACK_HOURS = 25
DUPLICATE_HOUR_COUNT = 2
SIGNIFICANT_DAYS_OLD = 1

# Boundary conditions
MIN_QUARTER_HOUR_BOUNDARY_MINUTES = 5  # Minutes after a quarter-hour boundary to consider stale

# Time windows
API_STALE_THRESHOLD_MINUTES = 60  # Cache considered stale after this many minutes
API_SEVERELY_STALE_THRESHOLD_HOURS = 12  # Cache considered severely stale after this many hours
