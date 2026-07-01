import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../Config"

// GIS Purge activity
//
// Opens the Pt GIS valve for the configured duration with no deposition
// target — purges gas from the line.
//
// Exposed parameters:
//   • purge duration (seconds)
//   • chamber recovery (seconds) — post-purge vacuum wait
//
// Layout: two-column form grid (Label | Control)
//
// Backend coupling:
//   The spin boxes bind two-way to appController.rtWorkflow.* — values
//   are sourced from the runner (which loads them from QSettings on
//   startup) and pushed back on every edit. The runner's setters are
//   no-ops on equal values, breaking the binding feedback loop. The
//   parent page is responsible for ensuring this component is only
//   visible when the runner exists (see RoomTempPrepPage's enabled
//   binding to appController.isConnected).

GroupBox {

    id: root

    // --- Parameter values (read by the page / controller) ---
    readonly property int purgeDuration: gisPurgeSpinBox.value
    readonly property int chamberRecovery: chamberRecoverySpinBox.value

    // --- Activity header summary ---
    readonly property string parameterSummary: purgeDuration + "s"

    Layout.fillWidth: true
    focusPolicy: Qt.StrongFocus
    background: Rectangle {
        anchors.fill: parent
        color: "transparent"
    }

    GridLayout {

        id: paramsGrid
        anchors.fill: parent
        columns: 2
        rowSpacing: AppConfig.activityFormRowSpacing
        columnSpacing: AppConfig.activityFormColumnSpacing

        // ---- Row 1: Purge duration ----

        ToolTippedLabel {
            id: gisPurgeDurationLabel
            text: "Duration (s)"
            toolTipText: Strings.gisPurgeDurationLabelTooltip
        }

        CustomSpinBox {
            id: gisPurgeSpinBox
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
            from: AppConfig.gisPurgeDurationMin
            to: AppConfig.gisPurgeDurationMax
            // Source of truth is the workflow runner. The fallback to
            // AppConfig is used only during the brief startup window
            // before the runner exists (the page itself disables during
            // that window, so the user never sees this value).
            value: appController.rtWorkflow
                   ? appController.rtWorkflow.gisPurgeDuration
                   : AppConfig.gisPurgeDefaultDuration
            editable: true
            showArrows: true
            // onValueModified (user edits only), not onValueChanged
            // (fires on programmatic/binding/clamp changes too) — match
            // the sibling spin boxes (SputterCoat, GISDeposition) so the
            // binding above and the write-back here don't form a loop.
            onValueModified: {
                if (appController.rtWorkflow) {
                    appController.rtWorkflow.gisPurgeDuration = value
                }
            }
        }

        // ---- Row 2: Chamber recovery ----

        ToolTippedLabel {
            id: chamberRecoveryLabel
            text: "Chamber Recovery (s)"
            toolTipText: Strings.chamberRecoveryLabelTooltip
        }

        CustomSpinBox {
            id: chamberRecoverySpinBox
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
            from: AppConfig.chamberRecoverySpinBoxMin
            to: AppConfig.chamberRecoverySpinBoxMax
            value: appController.rtWorkflow
                   ? appController.rtWorkflow.gisPurgeChamberRecovery
                   : AppConfig.chamberRecoverySpinBoxDefaultGISPurgeDuration
            editable: true
            showArrows: true
            // onValueModified (user edits only) — see the duration spin
            // box above for the rationale.
            onValueModified: {
                if (appController.rtWorkflow) {
                    appController.rtWorkflow.gisPurgeChamberRecovery = value
                }
            }
        }

    }

}
