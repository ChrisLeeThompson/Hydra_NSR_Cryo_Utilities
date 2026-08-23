import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../Config"
import "../Components"

// Room Temperature Preparation Page with activities related to preparing
// the microscope when the stage is at room temperature. Activities
// include GIS Purge and Home Stage.
//
// Layout note: the outer ColumnLayout uses a 12 px page-region spacing
// (heading → description → spacer → activity block → action buttons).
// The activity-to-activity gap inside the activity block is tightened to
// AppConfig.activityGap (8 px) to match the cryo page, which uses the
// same gap value for its delegates and drop-indicator math.
//
// Backend wiring:
//
//   • Page disable: the StackLayout in main.qml binds to
//     appController.isConnected, so this page is non-interactive until
//     the workflow runners exist. We additionally disable when another
//     page's workflow is running.
//
//   • Parameter binding lives in the leaf components (GISPurge.qml).
//     Each spin box reads from and writes to
//     appController.rtWorkflow.* directly. The page no longer needs
//     onChanged handlers or a Component.onCompleted seed — values are
//     loaded from QSettings on the runner's construction.
//
//   • Switch state pushes from QML to Python (Pattern B): switch
//     toggles aren't persisted, so they don't have a Python source of
//     truth to two-way-bind to. The runner consults the current
//     enabled flag at the start of each activity, so toggling a
//     switch off mid-workflow opts that activity out of the run if
//     it hasn't started yet.
//
//   • Switch enable rule: a switch is enabled while no workflow is
//     running, OR while the activity is still in "idle" state during
//     a run (hasn't started yet). Once an activity has begun or
//     finished within a run, its switch locks until the workflow
//     ends. The ActivityContainer's contentArea handles parameter
//     locking automatically — children are disabled when the
//     activity is in the "running" state.
//
//   • Activity-status routing: the runner emits activityStatusChanged
//     (activity_id, status); we dispatch to the matching container.
//     Containers keep their final state ("complete" / "stop" /
//     "exception") after a run for visual record, and reset to
//     "idle" only when the next workflow starts.
//
//   • Start / Stop drive appController.rtWorkflow.start() / stop().
//     Restore Defaults resets parameter values via the runner's
//     reset_parameters() slot.

Item {

    id: root

    // See CryoTempPrepPage.qml for why this binds to the property
    // directly rather than calling is_page_blocked().
    enabled: !appController.runningWorkflowId
             || appController.runningWorkflowId === "rt_prep"

    // ----------------------------------------------------------------------
    // Workflow → UI: route per-activity status, and reset containers at
    // the start of each run.
    // ----------------------------------------------------------------------
    Connections {
        target: appController.rtWorkflow
        ignoreUnknownSignals: true

        function onWorkflowStarted() {
            // Reset activity containers to "idle" at the start of each
            // workflow run — but only those about to run (switched on).
            // A completed activity that auto-disabled keeps its "complete"
            // badge until the user re-arms it (its onToggled clears it),
            // so it isn't wiped here. ``statusMessage`` is also cleared so
            // a stale tooltip from the previous run doesn't briefly surface
            // on the new run's idle icon.
            if (gisPurgeActivityContainer.switchChecked) {
                gisPurgeActivityContainer.activityState = "idle"
                gisPurgeActivityContainer.statusMessage = ""
            }
            if (homeStageActivityContainer.switchChecked) {
                homeStageActivityContainer.activityState = "idle"
                homeStageActivityContainer.statusMessage = ""
            }
        }

        function onActivityStatusChanged(activity_id, instance_id, status) {
            // Route the state to the right container, then compose the
            // status-icon tooltip text via the runner's Q_INVOKABLE
            // accessors. We compose here (rather than have the runner
            // emit a "duration reported" signal) because the page is
            // already routing on this signal — adding two query calls
            // is smaller than introducing a second signal.
            let container = null
            if (activity_id === "gis_purge") {
                container = gisPurgeActivityContainer
            } else if (activity_id === "home_stage") {
                container = homeStageActivityContainer
            } else {
                return
            }
            container.activityState = status

            if (status === "complete") {
                // Auto-disable on success so a completed activity doesn't
                // re-run on the next Start; the "complete" badge stays
                // until the user re-arms it (the container's onToggled).
                // Setting switchChecked here does not emit the container's
                // toggled signal, so push the enabled-state change to the
                // workflow explicitly via its matching Property.
                container.switchChecked = false
                if (activity_id === "gis_purge") {
                    appController.rtWorkflow.gisPurgeEnabled = false
                } else if (activity_id === "home_stage") {
                    appController.rtWorkflow.homeStageEnabled = false
                }

                // Tooltip: "Total duration: M min S s". The "Total"
                // qualifier disambiguates from per-step parameter
                // durations the user typed into spinboxes (purge
                // duration, chamber recovery), and mirrors the
                // workflow-level status-bar message vocabulary.
                const dur = appController.rtWorkflow.activityDuration(
                    activity_id, instance_id,
                )
                container.statusMessage = "Total duration: "
                    + appController.rtWorkflow.formatDuration(dur)
            } else if (status === "exception") {
                // Tooltip: the activity's last user-facing status
                // message, falling back to a generic "see log" hint
                // if none was captured. The full traceback lives in
                // the log; tooltips are too narrow for it.
                const msg = appController.rtWorkflow.lastStatusMessage(
                    activity_id, instance_id,
                )
                container.statusMessage = (msg !== "")
                    ? msg
                    : "Failed (see console log)"
            }
            // status === "stop": ActivityContainer renders no icon for
            // this state, so no tooltip is composed. Whatever message
            // the activity last pushed to the StatusBar carries the
            // stop reason; the per-row container stays quiet.
        }
    }

    ColumnLayout {

        anchors.fill: parent
        anchors.margins: AppConfig.pageMargin
        spacing: AppConfig.pageSectionSpacing

        Label {

            text: "Room Temperature Preparation"
            font.pixelSize: AppConfig.pageHeadingFontSize
            font.bold: true

        }

        Label {

            visible: !UiState.compact
            text: "Activities for preparing the Hydra NSR for cryo work, intended for use "
                + "while the stage is at room temperature. "
                + "Activities run from the top of the list down."
            font.pixelSize: AppConfig.pageBodyFontSize
            Layout.fillWidth: true
            wrapMode: Label.WordWrap

        }

        Item {
            Layout.preferredHeight: UiState.compact
                                    ? 0 : AppConfig.pageHeadingSpacerHeight
        }

        // --- Activity block ---
        ColumnLayout {

            Layout.fillWidth: true
            Layout.alignment: Qt.AlignHCenter
            spacing: AppConfig.activityGap

            ActivityContainer {

                id: gisPurgeActivityContainer
                title: "GIS Purge"
                titleToolTip: Strings.gisPurgeActivityTooltip
                Layout.fillWidth: true
                Layout.maximumWidth: AppConfig.activityContainerMaxWidthRoomTemp
                Layout.alignment: Qt.AlignHCenter
                expanded: true
                parameterSummary: gisPurgeActivity.parameterSummary
                stackHeader: true

                // Switch is enabled while the activity hasn't started
                // running yet. Once an activity has begun (or finished)
                // in the current workflow, its switch is locked. Between
                // workflow runs, all switches are unlocked. This lets
                // the user opt an activity out of an in-progress workflow
                // before its turn comes — the runner consults the current
                // enabled flag at the start of each activity.
                //
                // `activityState` is referenced unqualified to avoid a
                // self-reference binding loop warning that QML emits when
                // a property's binding reads another property on the same
                // object via id.
                switchEnabled: !appController.rtWorkflow
                               || !appController.rtWorkflow.isRunning
                               || activityState === "idle"

                onToggled: function(enabled) {
                    if (appController.rtWorkflow) {
                        appController.rtWorkflow.gisPurgeEnabled = enabled
                    }
                    // Re-arming a completed activity clears its "complete"
                    // badge so it reads as idle/ready again.
                    if (enabled) {
                        gisPurgeActivityContainer.activityState = "idle"
                        gisPurgeActivityContainer.statusMessage = ""
                    }
                }

                GISPurge {

                    id: gisPurgeActivity
                    Layout.fillWidth: true

                }

            }

            ActivityContainer {

                id: homeStageActivityContainer
                title: "Home Stage"
                titleToolTip: Strings.homeStageActivityTooltip
                Layout.fillWidth: true
                Layout.maximumWidth: AppConfig.activityContainerMaxWidthRoomTemp
                Layout.alignment: Qt.AlignHCenter

                // Same enable rule as GIS Purge — see comment above.
                switchEnabled: !appController.rtWorkflow
                               || !appController.rtWorkflow.isRunning
                               || activityState === "idle"

                onToggled: function(enabled) {
                    if (appController.rtWorkflow) {
                        appController.rtWorkflow.homeStageEnabled = enabled
                    }
                    // Re-arming a completed activity clears its "complete"
                    // badge so it reads as idle/ready again.
                    if (enabled) {
                        homeStageActivityContainer.activityState = "idle"
                        homeStageActivityContainer.statusMessage = ""
                    }
                }

            }

        }

        Item { Layout.fillHeight: true }

        RowLayout {

            Layout.fillWidth: true
            spacing: AppConfig.buttonRowSpacing

            Item { Layout.fillWidth: true }

            RoundButton {

                id: startButton
                text: "Start"
                radius: AppConfig.buttonRadius
                padding: AppConfig.buttonPadding

                // ToolTip.text: Strings.startButtonTooltip
                // ToolTip.visible: hovered
                // ToolTip.delay: AppConfig.toolTipDelayMs
                // ToolTip.timeout: AppConfig.toolTipTimeoutMs

                enabled: appController.isConnected
                         && appController.rtWorkflow
                         && appController.rtWorkflow.canStart

                onClicked: {
                    if (appController.rtWorkflow) {
                        appController.rtWorkflow.start()
                    }
                }

            }

            RoundButton {

                id: stopButton
                text: "Stop"
                radius: AppConfig.buttonRadius
                padding: AppConfig.buttonPadding

                ToolTip.text: Strings.stopButtonTooltip
                ToolTip.visible: hovered
                ToolTip.delay: AppConfig.toolTipDelayMs
                ToolTip.timeout: AppConfig.toolTipTimeoutMs

                enabled: appController.rtWorkflow
                         && appController.rtWorkflow.isRunning

                onClicked: {
                    if (appController.rtWorkflow) {
                        appController.rtWorkflow.stop()
                    }
                }

            }

        }

    }

}
