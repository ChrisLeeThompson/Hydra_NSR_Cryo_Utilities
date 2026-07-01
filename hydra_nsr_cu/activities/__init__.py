"""Activity services.

An "activity" is a single procedure executed against the microscope —
e.g. a GIS purge, a home stage, a sputter coat. Each activity is
implemented as an :class:`ActivityService` subclass that knows how to
run itself given hardware-ops dependencies and per-run parameters.

Workflows (see :mod:`hydra_nsr_cu.workflows`) compose activities into
ordered sequences and run them on a worker thread. Activities themselves
are agnostic about how they're scheduled; they just take a stop event
and progress callbacks and execute synchronously when called.
"""