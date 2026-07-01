"""Workflow runners.

A workflow is an ordered sequence of activities executed against the
microscope. The :class:`WorkflowRunner` base class handles the threading,
signal plumbing, and start/stop lifecycle that's common to all workflows.
Page-specific subclasses (RTWorkflow, CPWorkflow) add the parameters and
properties their QML pages need.

Threading model
---------------
Each workflow run gets a fresh :class:`QThread` and a fresh
``_WorkflowWorker``, set up in ``WorkflowRunner.start()``. The worker
runs activities sequentially on the worker thread. State changes are
emitted via Qt signals; the runner forwards them to QML.

Stop is signaled via a ``threading.Event`` shared between the runner
and worker. Activities check the event at safe interruption points
(see :class:`hydra_nsr_cu.activities.base.ActivityService`).
"""