import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Universal
import QtQuick.Layouts
import QtQuick.Window
import "./Components"
import "./Config"
import "./Dialogs"
import "./Pages"

ApplicationWindow {

    id: app_window
    title: Strings.mainWindowTitle
    width: AppConfig.mainWindowWidth
    height: AppConfig.mainWindowHeight
    // Permanent floor is the compact minimum, so the user can freely drag the
    // window down into the compact layout (see UiState.compact below). The
    // initial open size stays the normal mainWindow* size above.
    minimumWidth: AppConfig.compactWindowMinimumWidth
    minimumHeight: AppConfig.compactWindowMinimumHeight
    Universal.theme: AppConfig.universalTheme
    Universal.accent: AppConfig.universalAccent
    Universal.foreground: AppConfig.universalForeground
    Universal.background: AppConfig.universalBackground
    visible: true
    flags: {
        var base = Qt.Window
                  | Qt.WindowTitleHint
                  | Qt.WindowSystemMenuHint
                  | Qt.WindowMinMaxButtonsHint
                  | Qt.WindowCloseButtonHint
        return appController.settings.alwaysOnTop
               ? (base | Qt.WindowStaysOnTopHint)
               : base
    }

    readonly property var pageIndex: ({
                                          rt_prep: 0,
                                          cryo_prep: 1,
                                          stage_scan: 2,
                                          lo_calc: 3,
                                          milling_angle_calc: 4,
                                          session_log: 5,
                                          settings: 6
                                      })

    // --- Responsive compact layout -------------------------------------------
    //
    // The compact layout (subtitles hidden, header spacers collapsed) is driven
    // reactively from the live window size: UiState.compact is true whenever the
    // window has shrunk to the cutoff in either dimension, and every page reads
    // it for its subtitle visibility and header-spacer height. Because the window
    // minimums are the compact minimums (see minimumWidth/Height above), the user
    // can drag the window down into the compact range at any time.
    //
    // One-way and loop-free: reading width/height here and hiding subtitles only
    // reflows content inside the fixed window — it never changes the window size,
    // so it can't feed back across the threshold.
    Binding {
        target: UiState
        property: "compact"
        value: app_window.width <= AppConfig.compactBreakpointWidth
               || app_window.height <= AppConfig.compactBreakpointHeight
    }

    ColumnLayout {

        anchors.fill: parent
        spacing: 0

        RowLayout {

            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            SideBar {

                id: mainSideBar
                Layout.preferredWidth: AppConfig.sideBarWidth
                Layout.fillHeight: true

                primaryModel: ListModel {
                    ListElement { name: "RT Prep"; }
                    ListElement { name: "Cryo Prep"; }
                    ListElement { name: "Stage / Scan"; }
                    ListElement { name: "Lift-out Angle Calc"; }
                    ListElement { name: "Milling Angle Calc"; }
                }

                secondaryModel: ListModel {
                    ListElement { name: "Session Log" }
                    ListElement { name: "Settings" }
                }

            }

            StackLayout {

                id: mainStackLayout
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: mainSideBar.currentPageIndex
                // Pages can't interact with the controllers until the
                // microscope client and workflow runners exist. The
                // StatusBar shows "Connecting..." during this window.
                enabled: appController.isConnected

                RoomTempPrepPage {

                    id: roomTempPrepPage

                }

                CryoTempPrepPage {

                    id: cryoTempPrepPage

                }

                StageScanPage {

                    id: stageScanPage

                }

                LiftoutCalcPage {

                    id: liftoutCalcPage

                }

                MillingAngleCalcPage {

                    id: millingAngleCalcPage

                }


                SessionLogPage {

                    id: sessionLogPage

                }

                Settings {

                    id: settingsPage

                }

            }

        }

        StatusBar {

            id: mainStatusBar
            Layout.fillWidth: true
            statusIndicator: appController.connectionStatus

            // Multiplexed runner: whichever workflow is currently
            // running drives the StatusBar. Outside of a workflow run
            // this is null, and the bindings below null-guard
            // accordingly (Connections accepts a null target; the
            // `busy` line already null-checks).
            //
            // We multiplex on appController.runningWorkflowId rather
            // than on each workflow's isRunning Property because the
            // app guarantees only one workflow runs at a time (cross-
            // page disable enforces this), so a single-source-of-truth
            // id is the cleaner binding. The "stage_move" id has no
            // entry here — stage moves drive the StatusBar through a
            // separate Connections block below, since they aren't
            // workflows and don't share the runner signal surface.
            property var _activeRunner: {
                switch (appController.runningWorkflowId) {
                    case "rt_prep":   return appController.rtWorkflow
                    case "cryo_prep": return appController.cpWorkflow
                    default:          return null
                }
            }

            message: ""              // transient messages still drive this
            busy: _activeRunner ? _activeRunner.isRunning : false

            Connections {
                target: mainStatusBar._activeRunner
                ignoreUnknownSignals: true
                function onStatusUpdated(text) {
                    mainStatusBar.message = text
                }
                function onProgressUpdated(current, total) {
                    // Three cases:
                    //
                    //   (-1, -1) — sentinel for "indeterminate". An
                    //              activity is in an uninterruptible
                    //              opaque operation (home, sputter run);
                    //              show animated stripes.
                    //
                    //   (0, 0)   — degenerate; activity has a zero-length
                    //              phase. Ignore so the bar keeps its
                    //              current state rather than flashing.
                    //
                    //   (c, t>0) — normal progress; bar fills to c/t.
                    if (current === -1 && total === -1) {
                        mainStatusBar.indeterminate = true
                    } else if (total > 0) {
                        mainStatusBar.progress = current / total
                        mainStatusBar.indeterminate = false
                    }
                }
                function onIsRunningChanged() {
                    // Null guard: with the multiplexed _activeRunner,
                    // there's a brief window during workflow teardown
                    // where the runner's isRunningChanged signal has
                    // fired but appController.runningWorkflowId
                    // hasn't yet flipped to "". In practice today
                    // Qt's signal ordering means _activeRunner still
                    // points at the workflow at this slot's
                    // execution time, but the null check protects
                    // against future signal-order changes.
                    if (!mainStatusBar._activeRunner) {
                        return
                    }
                    if (mainStatusBar._activeRunner.isRunning) {
                        // A new run is starting. Cancel any pending
                        // reset from the previous run — without this,
                        // a Start within 3s of the previous workflow's
                        // end causes the timer to fire mid-run and
                        // visibly reset the progress bar to 0.
                        statusBarResetTimer.stop()
                    } else {
                        // Run ended — schedule a reset of the bar.
                        statusBarResetTimer.restart()
                    }
                }
            }

            // Stage move status — separate from the workflow runner since
            // stage moves are not workflows. Status text from the move
            // worker drives the same StatusBar.message slot. The
            // statusBarResetTimer is shared between the two so a move
            // and a workflow can't fight each other for the bar state.
            Connections {
                target: appController.stagePositions
                ignoreUnknownSignals: true
                function onStatusUpdated(text) {
                    mainStatusBar.message = text
                }
                function onIsMovingChanged() {
                    if (appController.stagePositions.isMoving) {
                        statusBarResetTimer.stop()
                    } else {
                        statusBarResetTimer.restart()
                    }
                }
            }

            // Stage rotation status — same shape as the stage-move block
            // above. Stage rotation is a manual-assist operation, not a
            // workflow, so it doesn't go through the multiplexed
            // _activeRunner switch. Z slider activity is intentionally
            // not routed here: per Q15 it only emits status text on
            // errors (which the user-visible "Stage Z error: ..."
            // message already conveys), and routing every slider
            // gesture would spam the StatusBar.
            Connections {
                target: appController.stageScan
                ignoreUnknownSignals: true
                function onStatusUpdated(text) {
                    mainStatusBar.message = text
                }
                function onIsRotatingChanged() {
                    if (appController.stageScan.isRotating) {
                        statusBarResetTimer.stop()
                    } else {
                        statusBarResetTimer.restart()
                    }
                }
            }

            // Cryo template feature — clean-load and save-success are
            // transient status-bar messages; the adjusted/failed cases
            // open modals from CryoTempPrepPage.qml.
            Connections {
                target: appController.cryoActivities
                ignoreUnknownSignals: true

                function onTemplateLoadSucceeded(templateName, issues) {
                    if (issues.length > 0) {
                        return  // adjusted-load: handled by the page modal
                    }
                    const count = appController.cryoActivities.model.count
                    const word = count === 1 ? "activity" : "activities"
                    mainStatusBar.showMessage(
                        "Template '" + templateName + "' loaded — " +
                        count + " " + word + ".",
                        5000
                    )
                }

                function onTemplateSaveSucceeded(templateName) {
                    mainStatusBar.showMessage(
                        "Template '" + templateName + "' saved.",
                        5000
                    )
                }
            }

            // Session log write failures — surface as a transient
            // status-bar message so the user knows the disk write
            // didn't land (file locked by Excel, disk full, etc.)
            // while the workflow proceeds unaffected. The in-memory
            // model still updates regardless, so the Session Log
            // page itself remains accurate during the gap.
            Connections {
                target: appController.sessionLog
                ignoreUnknownSignals: true

                function onWriteFailed(message) {
                    mainStatusBar.showMessage(message, 5000)
                }
            }

            Timer {
                id: statusBarResetTimer
                interval: 3000
                repeat: false
                onTriggered: {
                    mainStatusBar.progress = 0
                    mainStatusBar.indeterminate = false
                }
            }

        }

    }

    // --- Pre-start check dialogs ---
    //
    // The pre-start check system fires two kinds of signals on
    // CPWorkflow, RTWorkflow, StageScanController, and
    // StagePositionsController:
    //
    //   * preStartCheckRefused(checkItems)
    //       Hard veto. Open the dismiss-only dialog so the user
    //       sees why the start was refused; no further action
    //       possible. The originating controller has already
    //       emitted a "Pre-start check failed" status breadcrumb.
    //
    //   * preStartCheckNeedsConfirmation(checkItems)
    //       Soft warning. Open the OK/Cancel dialog. On accept
    //       or reject, call the originating controller's
    //       respondToConfirmation(bool) slot. The controller
    //       tracks which start is pending and either spawns the
    //       worker (accept) or emits a cancelled breadcrumb
    //       (reject).
    //
    //   The payload in both cases is a list of { title, message }
    //   objects (one per check result), produced Python-side by
    //   pre_start_checks.to_dialog_items. Handlers assign it to
    //   ConfirmDialog.checkItems, which renders one warning-icon
    //   row per entry.
    //
    // The two dialogs share the same ConfirmDialog component —
    // only the dismissOnly flag and the routing of accepted /
    // rejected differ. The confirm dialog tracks which controller
    // is awaiting its response via the `responder` property.

    ConfirmDialog {

        id: preStartRefuseDialog

        // The QML default parent for a top-level Dialog inside
        // ApplicationWindow is the window itself, which is what
        // ConfirmDialog's `anchors.centerIn: parent` wants. No
        // extra parenting needed.

        title: Strings.preStartRefuseTitle
        dismissOnly: true
        // checkItems is set by the openers below before each .open()
    }

    ConfirmDialog {

        id: preStartConfirmDialog

        title: Strings.preStartConfirmTitle
        // The accept button reads "Acknowledge and Continue" rather
        // than the default "Ok" to make the override semantics
        // explicit — clicking accepts the warning and proceeds with
        // the operation. The reject button keeps the default
        // "Cancel" since "cancel" is universally understood as
        // "don't do anything."
        acceptText: Strings.preStartConfirmAcceptText

        // Reference to the controller that emitted the
        // preStartCheckNeedsConfirmation signal — used to route
        // the user's response back to the originator via
        // respondToConfirmation(bool). Set by the opener; cleared
        // in onAccepted / onRejected after the call returns. Null
        // when no dialog is in flight.
        //
        // Typed as QtObject (rather than the concrete controller
        // class) because four different controller types share
        // the same signal/slot protocol — the slot is duck-typed
        // and QtObject is the closest QML can express to "any
        // object with a respondToConfirmation(bool) Slot."
        property QtObject responder: null

        onAccepted: {
            if (responder) {
                responder.respondToConfirmation(true)
                responder = null
            }
        }
        onRejected: {
            if (responder) {
                responder.respondToConfirmation(false)
                responder = null
            }
        }
    }

    // --- Pre-start check signal wiring ---
    //
    // Four Connections blocks — one per controller that
    // participates in the pre-start check protocol. All four
    // share the same signal names and handler bodies; only the
    // identity of the controller (used as the responder) differs
    // between blocks. Kept as four explicit blocks rather than
    // a factored-out Instantiator: the existing wiring
    // vocabulary in this file favors explicit listings and reads
    // more naturally to someone scanning the file. Revisit if a
    // fifth consumer appears.
    //
    // The `ignoreUnknownSignals: true` flag is defensive — it
    // means a future build that drops one of these signals from
    // a controller won't break the load. The flag matches the
    // pattern used by the other Connections blocks above.

    Connections {
        target: appController.rtWorkflow
        ignoreUnknownSignals: true

        function onPreStartCheckRefused(items) {
            preStartRefuseDialog.checkItems = items
            preStartRefuseDialog.open()
        }
        function onPreStartCheckNeedsConfirmation(items) {
            preStartConfirmDialog.responder = appController.rtWorkflow
            preStartConfirmDialog.checkItems = items
            preStartConfirmDialog.open()
        }
    }

    Connections {
        target: appController.cpWorkflow
        ignoreUnknownSignals: true

        function onPreStartCheckRefused(items) {
            preStartRefuseDialog.checkItems = items
            preStartRefuseDialog.open()
        }
        function onPreStartCheckNeedsConfirmation(items) {
            preStartConfirmDialog.responder = appController.cpWorkflow
            preStartConfirmDialog.checkItems = items
            preStartConfirmDialog.open()
        }
    }

    Connections {
        target: appController.stageScan
        ignoreUnknownSignals: true

        function onPreStartCheckRefused(items) {
            preStartRefuseDialog.checkItems = items
            preStartRefuseDialog.open()
        }
        function onPreStartCheckNeedsConfirmation(items) {
            preStartConfirmDialog.responder = appController.stageScan
            preStartConfirmDialog.checkItems = items
            preStartConfirmDialog.open()
        }
    }

    Connections {
        target: appController.stagePositions
        ignoreUnknownSignals: true

        function onPreStartCheckRefused(items) {
            preStartRefuseDialog.checkItems = items
            preStartRefuseDialog.open()
        }
        function onPreStartCheckNeedsConfirmation(items) {
            preStartConfirmDialog.responder = appController.stagePositions
            preStartConfirmDialog.checkItems = items
            preStartConfirmDialog.open()
        }
    }

}

