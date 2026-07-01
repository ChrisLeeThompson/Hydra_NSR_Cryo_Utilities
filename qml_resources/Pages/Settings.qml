import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../Config"
import "../Components"
import "../Dialogs"

// Settings page
//
// All controls bind to appController.settings.* (PySide SettingsController).
// Two-way binding is achieved by:
//   1. Reading the property as the initial value (.checked / .value / .text).
//   2. Writing back on the appropriate change signal.
// The SettingsController's setters are no-ops when the incoming value matches
// the stored value, so there is no feedback loop when a control echoes a
// value back to its source.
//
// Persistence is handled inside SettingsController — every successful set
// writes to QSettings immediately, no explicit save action required.

Item {

    id: root

    TapHandler {
        onTapped: root.forceActiveFocus()
    }

    ColumnLayout {

        anchors.fill: parent
        anchors.margins: AppConfig.pageMargin
        spacing: AppConfig.pageSectionSpacing

        Label {

            text: "Settings"
            font.pixelSize: AppConfig.pageHeadingFontSize
            font.bold: true

        }

        Label {

            // Subtitle: hidden in compact mode (title kept). A collapsed
            // Label yields its slot in the ColumnLayout, so the header
            // reflows with no gap.
            visible: !appController.settings.compactMode
            text: "Persistent script settings."
            font.pixelSize: AppConfig.pageBodyFontSize
            Layout.fillWidth: true
            wrapMode: Label.WordWrap

        }

        // Header spacer — collapsed in compact mode for a tighter header.
        Item {
            Layout.preferredHeight: appController.settings.compactMode
                                    ? 0 : AppConfig.pageHeadingSpacerHeight
        }

        // The settings form is wrapped in a ScrollView so it never clips at
        // the compact window height (this is the one page whose body isn't
        // already a scrolling list/view, and it hosts the Compact mode
        // toggle, so it must stay reachable at any size).
        ScrollView {

            id: settingsScrollView
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true

            GridLayout {

                id: mainGridLayout
                // Not Layout.* (a ScrollView's content item isn't in a
                // Layout): size the form to the available width, capped at
                // the same max width the page used before.
                width: Math.min(settingsScrollView.availableWidth,
                                AppConfig.settingsPageMaxWidth)
                columns: 2
                rowSpacing: AppConfig.settingsFormRowSpacing
                columnSpacing: AppConfig.settingsFormColumnSpacing

                // ---- Row 0: Always On Top ----

                ToolTippedLabel {

                    id: alwaysOnTopLabel
                    Layout.row: 0
                    Layout.column: 0
                    text: "Always On Top"
                    toolTipText: Strings.alwaysOnTopLabelTooltip

                }

                CheckBox {

                    id: alwaysOnTopCheckBox
                    Layout.row: 0
                    Layout.column: 1
                    Layout.alignment: Qt.AlignRight
                    checked: appController.settings.alwaysOnTop
                    onToggled: appController.settings.alwaysOnTop = checked

                }

                // ---- Row 1: Compact Mode ----

                ToolTippedLabel {

                    id: compactModeLabel
                    Layout.row: 1
                    Layout.column: 0
                    text: "Compact Mode"
                    toolTipText: Strings.compactModeLabelTooltip

                }

                CheckBox {

                    id: compactModeCheckBox
                    Layout.row: 1
                    Layout.column: 1
                    Layout.alignment: Qt.AlignRight
                    checked: appController.settings.compactMode
                    onToggled: appController.settings.compactMode = checked

                }

                // ---- Row 2: GIS Gas Port Name ----

                ToolTippedLabel {

                    id: gisGasPortNameLabel
                    Layout.row: 2
                    Layout.column: 0
                    text: "GIS Gas Port Name"
                    toolTipText: Strings.gisGasPortNameLabelTooltip

                }

                TextField {

                    id: gisGasPortNameTextEdit
                    Layout.row: 2
                    Layout.column: 1
                    Layout.preferredWidth: 120
                    Layout.alignment: Qt.AlignRight
                    enabled: !appController.anyWorkflowRunning
                    // Initial value pulled from settings on load. We avoid a
                    // declarative `text: appController.settings.gisGasPortName`
                    // binding so user typing isn't fought by the binding while
                    // the field is focused.
                    text: appController.settings.gisGasPortName
                    onAccepted: focus = false
                    onEditingFinished: appController.settings.gisGasPortName = text

                }

                // ---- Row 3: Sputter Pattern File ----

                ToolTippedLabel {

                    id: sputterPatternFileLabel
                    Layout.row: 3
                    Layout.column: 0
                    text: "Sputter Pattern File"
                    toolTipText: Strings.sputterPatternFileLabelTooltip

                }

                TextField {

                    id: sputterPatternFileTextEdit
                    Layout.row: 3
                    Layout.column: 1
                    Layout.preferredWidth: 160
                    Layout.alignment: Qt.AlignRight
                    enabled: !appController.anyWorkflowRunning
                    // Initial value pulled from settings on load (not a
                    // declarative binding) so user typing isn't fought while
                    // the field is focused — same idiom as gisGasPortName.
                    text: appController.settings.sputterPatternFile
                    onAccepted: focus = false
                    onEditingFinished: appController.settings.sputterPatternFile = text

                }

                // ---- Row 4: Sputter Coat HFW (µm) ----

                ToolTippedLabel {

                    id: sputterCoatHfwLabel
                    Layout.row: 4
                    Layout.column: 0
                    text: "Sputter Coat HFW (µm)"
                    toolTipText: Strings.sputterCoatHfwLabelTooltip

                }

                CustomSpinBox {

                    id: sputterCoatHfwSpinBox
                    Layout.row: 4
                    Layout.column: 1
                    Layout.alignment: Qt.AlignRight
                    enabled: !appController.anyWorkflowRunning
                    from: AppConfig.sputterCoatHfwMin
                    to: AppConfig.sputterCoatHfwMax
                    value: appController.settings.sputterCoatHfwMicrons
                    editable: true
                    showArrows: true
                    onValueModified: appController.settings.sputterCoatHfwMicrons = value

                }

                // ---- Row 5: Sputter Coater Port Name ----

                ToolTippedLabel {

                    id: sputterCoatPortNameLabel
                    Layout.row: 5
                    Layout.column: 0
                    text: "Sputter Coater Port Name"
                    toolTipText: Strings.sputterCoatPortNameLabelTooltip

                }

                TextField {

                    id: sputterCoatPortNameTextEdit
                    Layout.row: 5
                    Layout.column: 1
                    Layout.preferredWidth: 160
                    Layout.alignment: Qt.AlignRight
                    enabled: !appController.anyWorkflowRunning
                    text: appController.settings.sputterCoatPortName
                    onAccepted: focus = false
                    onEditingFinished: appController.settings.sputterCoatPortName = text

                }

                // ---- Row 6: Bulk Sputtering Duration ----

                ToolTippedLabel {

                    id: bulkSputteringDefaultDurationLabel
                    Layout.row: 6
                    Layout.column: 0
                    text: "Bulk Sputtering Duration (s)"
                    toolTipText: Strings.bulkSputteringDefaultDurationLabelTooltip

                }

                CustomSpinBox {

                    id: bulkSputteringDefaultDurationSpinBox
                    Layout.row: 6
                    Layout.column: 1
                    Layout.alignment: Qt.AlignRight
                    enabled: !appController.anyWorkflowRunning
                    from: AppConfig.sputterCoatDurationMin
                    to: AppConfig.sputterCoatDurationMax
                    value: appController.settings.bulkSputterDuration
                    editable: true
                    showArrows: true
                    // onValueModified (user edits only), not onValueChanged —
                    // see the sibling spin boxes (SputterCoat, GISDeposition).
                    onValueModified: appController.settings.bulkSputterDuration = value

                }

                // ---- Row 7: Lamella Sputtering Duration ----

                ToolTippedLabel {

                    id: lamellaSputteringDefaultDurationLabel
                    Layout.row: 7
                    Layout.column: 0
                    text: "Lamella Sputtering Duration (s)"
                    toolTipText: Strings.lamellaSputteringDefaultDurationLabelTooltip

                }

                CustomSpinBox {

                    id: lamellaSputteringDefaultDurationSpinBox
                    Layout.row: 7
                    Layout.column: 1
                    Layout.alignment: Qt.AlignRight
                    enabled: !appController.anyWorkflowRunning
                    from: AppConfig.sputterCoatDurationMin
                    to: AppConfig.sputterCoatDurationMax
                    value: appController.settings.lamellaSputterDuration
                    editable: true
                    showArrows: true
                    // onValueModified (user edits only) — see the bulk
                    // sputtering duration spin box above.
                    onValueModified: appController.settings.lamellaSputterDuration = value

                }

                // ---- Row 8: Restore PFIB Voltage and Current ----
                //
                // Restores the ion beam's electrical state on a committed
                // run: high voltage, beam current, and beam on/off. Beam
                // on/off rides with this toggle (see PFIBConditionsRecorder),
                // so word the label/tooltip to mention the on/off restore.
                // Independent of the ion-species toggle below.

                ToolTippedLabel {

                    id: restoreOriginalPFIBVoltageAndCurrentLabel
                    Layout.row: 8
                    Layout.column: 0
                    text: "Restore PFIB Voltage and Current"
                    toolTipText: Strings.restoreOriginalPFIBVoltageAndCurrentLabelTooltip
                }

                CheckBox {

                    id: restoreOriginalPFIBVoltageAndCurrentCheckBox
                    Layout.row: 8
                    Layout.column: 1
                    Layout.alignment: Qt.AlignRight
                    enabled: !appController.anyWorkflowRunning
                    checked: appController.settings.restoreOriginalPFIBVoltageAndCurrent
                    onToggled: appController.settings.restoreOriginalPFIBVoltageAndCurrent = checked

                }

                // ---- Row 9: Restore PFIB Ion Species ----
                //
                // Restores the plasma gas (ion species) on a committed run.
                // Independent of the voltage/current toggle above — leave
                // this off to keep the species switched in for sputter
                // coating while still reverting voltage/current.

                ToolTippedLabel {

                    id: restoreOriginalPFIBIonSpeciesLabel
                    Layout.row: 9
                    Layout.column: 0
                    text: "Restore PFIB Ion Species"
                    toolTipText: Strings.restoreOriginalPFIBIonSpeciesLabelTooltip
                }

                CheckBox {

                    id: restoreOriginalPFIBIonSpeciesCheckBox
                    Layout.row: 9
                    Layout.column: 1
                    Layout.alignment: Qt.AlignRight
                    enabled: !appController.anyWorkflowRunning
                    checked: appController.settings.restoreOriginalPFIBIonSpecies
                    onToggled: appController.settings.restoreOriginalPFIBIonSpecies = checked

                }

                // ---- Row 10: Zero Tilt Before GIS Deposition ----
                //
                // When checked, each GIS Deposition activity tilts the stage
                // to zero degrees before moving to its deposition position,
                // so the XY/Z translation happens from a flat orientation.
                // Adds an extra stage move per deposition — leave off for
                // workflows that hop between nearby positions (e.g. grid 1 to
                // grid 2) where the tilt is just dead time.

                ToolTippedLabel {

                    id: zeroTiltBeforeGisDepositionLabel
                    Layout.row: 10
                    Layout.column: 0
                    text: "Zero Tilt Before GIS Deposition"
                    toolTipText: Strings.zeroTiltBeforeGisDepositionLabelTooltip
                }

                CheckBox {

                    id: zeroTiltBeforeGisDepositionCheckBox
                    Layout.row: 10
                    Layout.column: 1
                    Layout.alignment: Qt.AlignRight
                    enabled: !appController.anyWorkflowRunning
                    checked: appController.settings.zeroTiltBeforeGisDeposition
                    onToggled: appController.settings.zeroTiltBeforeGisDeposition = checked

                }

                // ---- Row 11: Move Stage To Original Position ----

                ToolTippedLabel {

                    id: moveStageToOriginalPositionLabel
                    Layout.row: 11
                    Layout.column: 0
                    text: "Move Stage To Original Position"
                    toolTipText: Strings.moveStageToOriginalPositionLabelTooltip
                }

                CheckBox {

                    id: moveStageToOriginalPositionCheckBox
                    Layout.row: 11
                    Layout.column: 1
                    Layout.alignment: Qt.AlignRight
                    enabled: !appController.anyWorkflowRunning
                    checked: appController.settings.moveStageToOriginalPosition
                    onToggled: appController.settings.moveStageToOriginalPosition = checked

                }

                // ---- Row 12: Reset Parameters For RT Prep Page ----

                ToolTippedLabel {

                    id: resetRTPrepParametersLabel
                    Layout.row: 12
                    Layout.column: 0
                    text: "Reset RT Prep Parameters"
                    toolTipText: Strings.resetRTPrepParametersLabelTooltip

                }

                RoundButton {

                    id: resetRTPrepParametersButton
                    Layout.row: 12
                    Layout.column: 1
                    Layout.alignment: Qt.AlignRight
                    text: "Reset RT Prep Parameters"
                    radius: AppConfig.buttonRadius

                    ToolTip.text: Strings.resetRTPrepParametersLabelTooltip
                    ToolTip.delay: AppConfig.toolTipDelayMs
                    ToolTip.timeout: AppConfig.toolTipTimeoutMs
                    ToolTip.visible: hovered

                    // Disable while ANY workflow is running (not just RT) —
                    // restoring defaults mid-run is confusing and the
                    // parameters are baked in to the running activity anyway.
                    // Matches the page-wide anyWorkflowRunning lock used by
                    // the other workflow-affecting controls. rtWorkflow must
                    // be non-null because reset_parameters lives on it (unlike
                    // the always-present cryoActivities).
                    enabled: appController.rtWorkflow
                             && !appController.anyWorkflowRunning

                    onClicked: {
                        if (appController.rtWorkflow) {
                            appController.rtWorkflow.reset_parameters()
                        }
                    }

                }

                // ---- Row 13: Reset Parameters For Cryo Prep Page ----

                ToolTippedLabel {

                    id: resetCryoPrepParametersLabel
                    Layout.row: 13
                    Layout.column: 0
                    text: "Reset Cryo Prep Parameters"
                    toolTipText: Strings.resetCryoPrepParametersLabelTooltip

                }

                RoundButton {

                    id: resetCryoPrepParametersButton
                    Layout.row: 13
                    Layout.column: 1
                    Layout.alignment: Qt.AlignRight
                    text: "Reset Cryo Prep Parameters"
                    radius: AppConfig.buttonRadius

                    ToolTip.text: Strings.resetCryoPrepParametersLabelTooltip
                    ToolTip.delay: AppConfig.toolTipDelayMs
                    ToolTip.timeout: AppConfig.toolTipTimeoutMs
                    ToolTip.visible: hovered

                    // Disable while ANY workflow is running — matches the
                    // page-wide anyWorkflowRunning lock. This acts on
                    // cryoActivities, which is always present (no microscope
                    // required), so the gate must NOT require cpWorkflow to be
                    // non-null: cpWorkflow is null until the client is
                    // constructed, and anyWorkflowRunning is false while
                    // offline/idle, so the button stays enabled during
                    // offline setup. The (cpWorkflow-absent OR not-running)
                    // clause keeps that offline-enable explicit even though
                    // anyWorkflowRunning already covers the run case.
                    enabled: (!appController.cpWorkflow
                              || !appController.cpWorkflow.isRunning)
                             && !appController.anyWorkflowRunning

                    onClicked: appController.cryoActivities.reset_parameters()
                }

                // ---- Row 14: Session Log Size ----

                ToolTippedLabel {

                    id: sessionLogSizeLabel
                    Layout.row: 14
                    Layout.column: 0
                    text: "Session Log File Size"
                    toolTipText: Strings.sessionLogSizeLabelTooltip

                }

                Label {

                    id: sessionLogSizeValueLabel
                    Layout.row: 14
                    Layout.column: 1
                    Layout.alignment: Qt.AlignRight
                    text: appController.sessionLog.formatFileSize(
                              appController.sessionLog.fileSize
                          )
                    font.pixelSize: AppConfig.pageBodyFontSize

                }

                // ---- Row 15: Clear Session Log ----

                ToolTippedLabel {

                    id: clearSessionLogLabel
                    Layout.row: 15
                    Layout.column: 0
                    text: "Clear Session Log"
                    toolTipText: Strings.clearSessionLogLabelTooltip

                }

                RoundButton {

                    id: clearSessionLogButton
                    Layout.row: 15
                    Layout.column: 1
                    Layout.alignment: Qt.AlignRight
                    text: "Clear Session Log"
                    radius: AppConfig.buttonRadius

                    ToolTip.text: Strings.clearSessionLogLabelTooltip
                    ToolTip.delay: AppConfig.toolTipDelayMs
                    ToolTip.timeout: AppConfig.toolTipTimeoutMs
                    ToolTip.visible: hovered

                    // Disable while a workflow is running. Same protection
                    // pattern as the Reset Parameters buttons above, but
                    // load-bearing here: ``clearAll()`` mid-workflow would
                    // orphan the in-flight session — the controller's
                    // current-session pointer becomes stale and subsequent
                    // ``on_activity_recorded`` events get dropped per its
                    // defense-in-depth check. Disabling at the UI prevents
                    // that footgun without special-casing in the
                    // controller. Also disabled when there's nothing to
                    // clear (file size is 0).
                    enabled: !appController.anyWorkflowRunning
                             && appController.sessionLog.fileSize > 0

                    onClicked: clearSessionLogConfirmDialog.open()

                }

            }

        }

    }

    // Destructive Clear Session Log confirmation. Lives as a sibling
    // of the ColumnLayout (not inside it) so the dialog overlays the
    // page correctly. Triggered from the Clear Session Log button's
    // onClicked; the dialog itself does the actual work via
    // ``onAccepted`` (the standard Qt Dialog signal — ConfirmDialog
    // doesn't expose a custom ``confirmed`` signal).
    //
    // ``destructive: true`` puts the focus default on Cancel rather
    // than OK — a non-reflexive gate the user has to deliberately
    // step past. Matches the design convention we agreed for any
    // irreversible action.
    //
    // Button labels are hardcoded inside ConfirmDialog ("Cancel" /
    // "Ok") rather than parameterized — the title and message carry
    // the intent.

    ConfirmDialog {

        id: clearSessionLogConfirmDialog
        title: Strings.clearSessionLogTitle
        message: Strings.clearSessionLogMessage
        destructive: true

        onAccepted: appController.sessionLog.clearAll()

    }

}
