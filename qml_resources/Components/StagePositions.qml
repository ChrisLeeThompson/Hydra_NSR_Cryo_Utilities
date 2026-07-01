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
//                       The controller reads the live stage position
//                       and appends a row.
//   • Edit Name       — opens a name dialog; on accept, calls
//                       appController.stagePositions.rename(index, name).
//                       Renames only — coordinates are unchanged.
//   • Move To         — opens a confirm dialog; on accept, calls
//                       appController.stagePositions.move_to(index).
//                       The move runs on a worker thread; observe via
//                       appController.stagePositions.isMoving and the
//                       moveStarted / moveFinished signals. The page
//                       hosting this component drives the parent
//                       ActivityContainer's activityState in response.
//   • Update Position — opens a confirm dialog; on accept, calls
//                       appController.stagePositions.update_from_current(index).
//                       The controller re-reads the stage and overwrites
//                       coordinates; name and id are preserved.
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
//   On move failure, the controller emits moveFinished(false) and a
//   generic error dialog opens directing the user to the log file
//   for details. Surfacing the AutoScript exception message in the
//   UI was considered and rejected — the messages can be cryptic
//   to non-developers and centralizing diagnostics in the log file
//   matches our broader pattern.

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
        function onMoveFinished(success) {
            if (!success) {
                moveErrorDialog.open()
            }
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
            //
            // The cross-page disable system handles the "another page's
            // workflow is running" case at the page level — this binding
            // doesn't need to consult it.
            enabled: appController.stagePositions !== null
                     && root._hasSelection
                     && !appController.stagePositions.isMoving

            ToolTip.text: Strings.stageMoveToButtonTooltip
            ToolTip.visible: hovered
            ToolTip.delay: AppConfig.toolTipDelayMs
            ToolTip.timeout: AppConfig.toolTipTimeoutMs

            onClicked: {
                moveToDialog.message = Strings.stageMoveToMessage.arg(root._selectedName)
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
    // block above. Generic message — actual error details are in the
    // log file.
    ConfirmDialog {
        id: moveErrorDialog
        title: Strings.stageMoveErrorTitle
        message: Strings.stageMoveErrorConfirmDialog
        dismissOnly: true
    }

}
