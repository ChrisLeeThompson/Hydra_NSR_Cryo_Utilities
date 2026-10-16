pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Universal
import QtQuick.Layouts
import "../Config"
import "../Dialogs"

// Stage Positions activity
//
// The activity contains a list of saved stage positions and five
// action buttons that operate on them (Add / Edit Name / Move To /
// Update Position / Remove Position). Layout mirrors the stage
// position module in TFS xT Microscope Control software: list view on
// the left, button column on the right.
//
// Backend interaction:
//
//   • Add Position    — opens a name dialog; on accept, calls
//                       appController.stagePositions.add_from_current(name).
//                       The controller reads the live stage position and
//                       the selected holder and appends a row. A holder
//                       outside SUPPORTED_HOLDERS opens "Holder Not
//                       Supported" instead (checked before the name
//                       dialog opens, and again by the controller).
//   • Edit Name       — opens a name dialog; on accept, calls
//                       appController.stagePositions.rename(index, name).
//                       Renames only — coordinates are unchanged.
//   • Move To         — opens the Move To dialog; on accept, calls
//                       appController.stagePositions.move_to(index). The
//                       controller selects the holder the position was
//                       saved with and leaves it selected; for the
//                       standard shuttle and spin mill holders the
//                       dialog says so. The move runs on a worker
//                       thread; observe via
//                       appController.stagePositions.isMoving and the
//                       moveStarted / moveFinished signals. The page
//                       hosting this component drives the parent
//                       ActivityContainer's activityState in response.
//   • Update Position — opens a confirm dialog; on accept, calls
//                       appController.stagePositions.update_from_current(index).
//                       The controller re-reads the stage and the holder
//                       and overwrites both; name and id are preserved.
//                       Same unsupported-holder check as Add Position.
//   • Remove Position — opens a confirm dialog; on accept, calls
//                       appController.stagePositions.remove(index).
//
// Persistence is handled inside the controller — every successful
// mutation writes the full list to QSettings as a JSON string.
//
// Coordinate units:
//   The model holds AutoScript SI units (meters for x/y/z, radians
//   for r/t). Display conversion to mm and degrees happens in the
//   delegate (StagePositionsListView).
//
// Stage Move error handling:
//   On move failure, the controller emits moveFinished(false, name,
//   reason) and an error dialog opens directing the user to the
//   console log; a shortfall (the stage stopped short of the position,
//   usually at the holder limits) and a holder that could not be
//   selected get their own messages. Surfacing the AutoScript exception
//   message in the UI was considered and rejected — the messages can be
//   cryptic to non-developers and centralizing diagnostics in the
//   console log matches our broader pattern.

GroupBox {

    id: root

    // --- Derived state ---
    //
    // The shared stage positions model is sourced from
    // appController.stagePositions.model. The page null-guards in case
    // the controller hasn't been constructed yet (brief startup window
    // before the microscope client resolves).

    readonly property var positionsModel:
        appController.stagePositions
            ? appController.stagePositions.model
            : null

    readonly property int _selectedIndex: stagePositionsListView.selectedIndex
    readonly property bool _hasSelection: _selectedIndex >= 0

    readonly property string _selectedName:
        _hasSelection && positionsModel
            ? positionsModel.get(_selectedIndex).name
            : ""

    // --- Helpers ---

    // Build a plain JS array of the current names in the model, for
    // duplicate-name validation in the Add / Edit dialogs. Rebuilt on
    // each invocation (cheap — the list is small and the dialog open
    // is user-driven).
    function _currentNames() {
        const out = []
        if (!positionsModel) return out
        for (let i = 0; i < positionsModel.count; ++i) {
            out.push(positionsModel.get(i).name)
        }
        return out
    }

    // Open "Holder Not Supported" for `holder` (xT's name), listing the
    // holders a position can be saved with.
    function _showHolderNotSupported(holder) {
        holderNotSupportedDialog.message = Strings.holderNotSupportedDialog
            .arg(holder)
            .arg(appController.stagePositions.supportedHoldersText)
        holderNotSupportedDialog.open()
    }

    // True (and the dialog opened) when the selected holder cannot be
    // saved with a position — checked before the Add / Update dialogs
    // so the operator doesn't type a name for nothing.
    function _refuseUnsupportedHolder() {
        const holder = appController.stagePositions.unsupported_holder()
        if (holder === "") return false
        root._showHolderNotSupported(holder)
        return true
    }

    Layout.fillWidth: true
    Layout.fillHeight: true

    focusPolicy: Qt.StrongFocus
    background: Rectangle {
        anchors.fill: parent
        color: "transparent"
    }

    // ----------------------------------------------------------------------
    // Move lifecycle: open the error dialog on failure. Success is
    // silent — the parent page resets the activity state to idle, the
    // StatusBar message clears via its own reset timer, and the user's
    // mental model just sees "I clicked Move To, the move happened, the
    // page is back to normal."
    // ----------------------------------------------------------------------
    Connections {
        target: appController.stagePositions
        ignoreUnknownSignals: true
        function onMoveFinished(success, name, reason) {
            if (!success) {
                if (reason === "shortfall") {
                    moveErrorDialog.message = Strings.stageMoveShortfallDialog.arg(name)
                } else if (reason === "holder") {
                    moveErrorDialog.message = Strings.stageMoveHolderErrorDialog.arg(name)
                } else {
                    moveErrorDialog.message = Strings.stageMoveErrorConfirmDialog
                }
                moveErrorDialog.open()
            }
        }
        // The controller re-checks the holder at save time; it changed
        // since the button's pre-check.
        function onHolderNotSupported(holder) {
            root._showHolderNotSupported(holder)
        }
    }

    GridLayout {

        id: mainGridLayout

        anchors.fill: parent
        rows: 5
        columns: 2
        rowSpacing: AppConfig.activityStagePositionsRowSpacing
        columnSpacing: AppConfig.activityStagePositionsColumnSpacing

        // --- List view (spans all 5 rows in column 0, left side) ---
        StagePositionsListView {
            id: stagePositionsListView
            Layout.rightMargin: AppConfig.activityStagePositionsListMargin
            Layout.rowSpan: 5
            Layout.column: 0
            Layout.fillWidth: true
            Layout.fillHeight: true

            model: root.positionsModel
        }

        RoundButton {
            id: stageAddPositionButton
            Layout.row: 0
            Layout.column: 1
            Layout.preferredWidth: AppConfig.stagePositionsButtonWidth
            text: "Add Position"
            radius: AppConfig.buttonRadius

            ToolTip.text: Strings.stageAddPositionButtonTooltip
            ToolTip.visible: hovered
            ToolTip.delay: AppConfig.toolTipDelayMs
            ToolTip.timeout: AppConfig.toolTipTimeoutMs

            // Disable until the controller exists. Without the
            // controller, Add has nowhere to send its result.
            enabled: appController.stagePositions !== null

            onClicked: {
                if (root._refuseUnsupportedHolder()) return
                addPositionDialog.existingNames = root._currentNames()
                addPositionDialog.initialName = ""
                addPositionDialog.open()
            }
        }

        RoundButton {
            id: stageEditNameButton
            Layout.row: 1
            Layout.column: 1
            Layout.preferredWidth: AppConfig.stagePositionsButtonWidth
            text: "Edit Name"
            radius: AppConfig.buttonRadius
            enabled: root._hasSelection && appController.stagePositions !== null

            ToolTip.text: Strings.stageEditNameButtonTooltip
            ToolTip.visible: hovered
            ToolTip.delay: AppConfig.toolTipDelayMs
            ToolTip.timeout: AppConfig.toolTipTimeoutMs

            onClicked: {
                editNameDialog.existingNames = root._currentNames()
                editNameDialog.initialName =
                    root.positionsModel.get(root._selectedIndex).name
                editNameDialog.open()
            }
        }

        RoundButton {
            id: stageMoveToButton
            Layout.row: 2
            Layout.column: 1
            Layout.preferredWidth: AppConfig.stagePositionsButtonWidth
            text: "Move To"
            radius: AppConfig.buttonRadius

            // Enabled when:
            //   * the controller exists (microscope has connected)
            //   * a row is selected
            //   * no move is currently in flight
            //   * no workflow is running
            //
            // The last guard matters on the Cryo Prep page, which stays
            // enabled during its own run: a manual move mid-workflow
            // would race the running activity and be undone by the
            // end-of-run stage restore. (anyWorkflowRunning also covers
            // an in-flight Move To, so the isMoving check is kept only
            // for the brief window before the move id is registered.)
            enabled: appController.stagePositions !== null
                     && root._hasSelection
                     && !appController.stagePositions.isMoving
                     && !appController.anyWorkflowRunning

            ToolTip.text: Strings.stageMoveToButtonTooltip
            ToolTip.visible: hovered
            ToolTip.delay: AppConfig.toolTipDelayMs
            ToolTip.timeout: AppConfig.toolTipTimeoutMs

            onClicked: {
                moveToDialog.message = Strings.stageMoveToMessage.arg(root._selectedName)
                // The standard shuttle and spin mill holders ask first:
                // the move selects the holder and leaves it selected.
                const info = root.positionsModel.holder_info(root._selectedIndex)
                moveToDialog.note = info.needsConfirmation
                    ? Strings.stageMoveToHolderNote.arg(info.holder)
                    : ""
                moveToDialog.open()
            }
        }

        RoundButton {
            id: stageUpdatePositionButton
            Layout.row: 3
            Layout.column: 1
            Layout.preferredWidth: AppConfig.stagePositionsButtonWidth
            text: "Update Position"
            radius: AppConfig.buttonRadius
            enabled: root._hasSelection && appController.stagePositions !== null

            ToolTip.text: Strings.stageUpdatePositionButtonTooltip
            ToolTip.visible: hovered
            ToolTip.delay: AppConfig.toolTipDelayMs
            ToolTip.timeout: AppConfig.toolTipTimeoutMs

            onClicked: {
                if (root._refuseUnsupportedHolder()) return
                updatePositionDialog.message = Strings.stageUpdatePositionMessage.arg(root._selectedName)
                updatePositionDialog.open()
            }
        }

        RoundButton {
            id: stageRemovePositionButton
            Layout.row: 4
            Layout.column: 1
            Layout.preferredWidth: AppConfig.stagePositionsButtonWidth
            text: "Remove Position"
            radius: AppConfig.buttonRadius
            enabled: root._hasSelection && appController.stagePositions !== null

            ToolTip.text: Strings.stageRemovePositionButtonTooltip
            ToolTip.visible: hovered
            ToolTip.delay: AppConfig.toolTipDelayMs
            ToolTip.timeout: AppConfig.toolTipTimeoutMs

            onClicked: {
                removePositionDialog.message = Strings.stageRemovePositionMessage.arg(root._selectedName)
                removePositionDialog.open()
            }
        }
    }

    // ------------------------------------------------------------------
    // Dialogs (one instance each, created with the activity)
    // ------------------------------------------------------------------

    PositionNameDialog {
        id: addPositionDialog
        title: "Add Position"
        onAcceptedName: (name) => {
            if (appController.stagePositions) {
                appController.stagePositions.add_from_current(name)
            }
        }
    }

    PositionNameDialog {
        id: editNameDialog
        title: "Edit Position Name"
        onAcceptedName: (name) => {
            if (appController.stagePositions) {
                appController.stagePositions.rename(root._selectedIndex, name)
            }
        }
    }

    ConfirmDialog {
        id: moveToDialog
        title: Strings.stageMoveToTitle
        onAccepted: {
            if (appController.stagePositions) {
                appController.stagePositions.move_to(root._selectedIndex)
            }
        }
    }

    ConfirmDialog {
        id: updatePositionDialog
        title: Strings.stageUpdatePositionTitle
        onAccepted: {
            if (appController.stagePositions) {
                appController.stagePositions.update_from_current(root._selectedIndex)
            }
        }
    }

    ConfirmDialog {
        id: removePositionDialog
        title: Strings.stageRemovePositionTitle
        destructive: true
        onAccepted: {
            if (appController.stagePositions) {
                appController.stagePositions.remove(root._selectedIndex)
            }
        }
    }

    // Single-button error dialog opened by the move-finished Connections
    // block above, which sets the message (generic failure, shortfall,
    // or holder not selected). Actual error details are in the console
    // log.
    ConfirmDialog {
        id: moveErrorDialog
        title: Strings.stageMoveErrorTitle
        message: Strings.stageMoveErrorConfirmDialog
        dismissOnly: true
    }

    // Add / Update Position refused: the selected holder is not one a
    // position can be saved with. The message names it and lists the
    // supported ones (see _showHolderNotSupported).
    ConfirmDialog {
        id: holderNotSupportedDialog
        title: Strings.holderNotSupportedTitle
        dismissOnly: true
    }

}
