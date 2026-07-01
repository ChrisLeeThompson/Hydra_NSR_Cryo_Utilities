import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Universal
import QtQuick.Layouts
import HydraNSR.Microscope 1.0
import "../Config"
import "../Components"
import "../Effects"

// Stage / Scan Page.
// This page includes functions to rotate the stage, scan rotate the SEM and FIB,
// and adjust the z height of the stage.

Item {

    id: root

    // Cross-page disable. The page enables itself when nothing is
    // running globally, or when the running operation is *this*
    // page's stage rotation. Other pages' workflows / stage moves
    // disable the entire page surface here.
    //
    // We bind to the runningWorkflowId Property directly rather than
    // calling the is_page_blocked() slot. Property reads in QML
    // trigger automatic re-evaluation when the source changes; Slot
    // calls don't.
    enabled: !appController.runningWorkflowId
             || appController.runningWorkflowId === "stage_rotation"

    // --- Busy state ---
    //
    // True while a stage rotation is in progress. Disables the rotate
    // and scan-rotate buttons, the Z slider, and the BusyIndicator
    // overlay on the rotate button. Bound to the backend so the
    // state automatically clears at the end of a rotation (and
    // tracks any future rotations started programmatically).
    //
    // Null-guarded against the brief construction window before
    // ``_create_microscope_dependent_services`` runs.
    property bool rotating: appController.stageScan
                            ? appController.stageScan.isRotating
                            : false

    // --- Z-slider safety gate ---
    //
    // Mirrors the controller's gate state, null-guarded for the brief
    // startup window before microscope services exist. "UNKNOWN" is the
    // fail-closed default: the slider stays disabled until the controller
    // reports an in-range reading, but -- unlike "BLOCKED" -- it does not
    // show the lock overlay. That keeps the overlay from flashing on the
    // first page activation, where the first safe-range reading arrives
    // about one worker tick after the page becomes visible.
    property string zSafetyState: appController.stageScan
                                  ? appController.stageScan.zSafetyState
                                  : "UNKNOWN"

    // The distance-aware out-of-range reason, shared verbatim with the
    // rotate / Move To confirm dialog (both come from
    // describe_out_of_range). Empty until the first poll; the overlay
    // falls back to a generic line in that brief window.
    property string zSafetyMessage: appController.stageScan
                                    ? appController.stageScan.zSafetyMessage
                                    : ""

    // Drive the safe-range poll only while this page is on screen and
    // interactive. Including ``stageScan`` here switches the poll on as
    // soon as the controller appears (it is null during startup); the
    // slot call is null-guarded for the teardown direction.
    property bool pageActive: !!appController.stageScan && visible && enabled
    onPageActiveChanged: if (appController.stageScan)
                             appController.stageScan.setStagePageActive(pageActive)

    ColumnLayout {

        anchors.fill: parent
        anchors.margins: AppConfig.pageMargin
        spacing: AppConfig.pageSectionSpacing

        Label {

            text: "Stage/Scan Rotation and Stage Z"
            font.pixelSize: AppConfig.pageHeadingFontSize
            font.bold: true

        }

        Label {

            text: "These functions can be used to rotate the stage 180°, scan rotate the SEM and FIB, and adjust the stage z height (with Z-Y Link enabled)."
                  + "<br><br><b>Note:</b> the stage rotation and z height functions are not aware of the position of the stage relative to the SEM or FIB polepieces, or the iFLM objective."
            font.pixelSize: AppConfig.pageBodyFontSize
            Layout.fillWidth: true
            wrapMode: Label.WordWrap

        }

        Item { Layout.preferredHeight: AppConfig.pageHeadingSpacerHeight }

        GridLayout {

            id: mainGridLayout
            Layout.alignment: Qt.AlignCenter
            rows: 7
            columns: 2
            rowSpacing: 12
            columnSpacing: 12

            RoundButton {

                id: stageRot180Button
                Layout.row: 0
                Layout.column: 0
                Layout.preferredWidth: AppConfig.stageScanBigButtonWidth
                Layout.preferredHeight: AppConfig.stageScanBigButtonHeight
                radius: AppConfig.buttonRadius
                padding: AppConfig.stageScanBigButtonPadding
                text: "Rotate Stage 180°"
                enabled: !root.rotating

                ToolTip.text: Strings.stageRot180ButtonTooltip
                ToolTip.visible: hovered
                ToolTip.delay: AppConfig.toolTipDelayMs
                ToolTip.timeout: AppConfig.toolTipTimeoutMs

                // Busy indicator overlay - shown while root.rotating is true.
                BusyIndicator {
                    anchors.centerIn: parent
                    width: parent.width * 0.6
                    height: width
                    running: root.rotating
                    visible: root.rotating
                }

                onClicked: {
                    if (appController.stageScan) {
                        appController.stageScan.start_rotation()
                    }
                }

            }

            ColumnLayout {

                id: scanRotateButtonColumnLayout
                Layout.row: 0
                Layout.column: 1
                Layout.maximumWidth: AppConfig.stageScanBigButtonWidth
                Layout.maximumHeight: AppConfig.stageScanBigButtonHeight
                spacing: 12

                RoundButton {

                    id: setScanRot0Button
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    radius: AppConfig.buttonRadius
                    padding: AppConfig.stageScanBigButtonPadding
                    text: "Scan Rotate SEM and FIB to 0°"
                    enabled: !root.rotating

                    ToolTip.text: Strings.setScanRot0ButtonTooltip
                    ToolTip.visible: hovered
                    ToolTip.delay: AppConfig.toolTipDelayMs
                    ToolTip.timeout: AppConfig.toolTipTimeoutMs

                    onClicked: {
                        if (appController.stageScan) {
                            appController.stageScan.set_scan_rotation_to_0()
                        }
                    }

                }

                RoundButton {

                    id: setScanRot180Button
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    radius: AppConfig.buttonRadius
                    padding: AppConfig.stageScanBigButtonPadding
                    text: "Scan Rotate SEM and FIB to 180°"
                    enabled: !root.rotating

                    ToolTip.text: Strings.setScanRot180ButtonTooltip
                    ToolTip.visible: hovered
                    ToolTip.delay: AppConfig.toolTipDelayMs
                    ToolTip.timeout: AppConfig.toolTipTimeoutMs

                    onClicked: {
                        if (appController.stageScan) {
                            appController.stageScan.set_scan_rotation_to_180()
                        }
                    }

                }

            }

            CheckBox {

                id: scanRotWithStageCheckBox
                Layout.row: 1
                Layout.column: 0
                text: "Scan Rotate After Rotation"
                checked: appController.settings.scanRotateAfterRotation
                onToggled: appController.settings.scanRotateAfterRotation = checked
                enabled: !root.rotating

                ToolTip.text: Strings.scanRotWithStageCheckBoxTooltip
                ToolTip.visible: hovered
                ToolTip.delay: AppConfig.toolTipDelayMs
                ToolTip.timeout: AppConfig.toolTipTimeoutMs

            }

            CheckBox {

                id: stageTiltWithRotCheckBox
                Layout.row: 2
                Layout.column: 0
                text: "Zero Tilt Before Rotation"
                checked: appController.settings.zeroTiltBeforeRotation
                onToggled: appController.settings.zeroTiltBeforeRotation = checked
                enabled: !root.rotating

                ToolTip.text: Strings.stageTiltWithRotCheckBoxTooltip
                ToolTip.visible: hovered
                ToolTip.delay: AppConfig.toolTipDelayMs
                ToolTip.timeout: AppConfig.toolTipTimeoutMs

            }

            RowLayout {

                Layout.row: 3
                Layout.columnSpan: 2

                CheckBox {

                    id: tiltAfterRotationCheckBox
                    text: "Tilt After Rotation"
                    checked: appController.settings.tiltAfterRotation
                    onToggled: appController.settings.tiltAfterRotation = checked
                    enabled: !root.rotating

                    ToolTip.text: Strings.tiltAfterRotationCheckBoxTooltip
                    ToolTip.visible: hovered
                    ToolTip.delay: AppConfig.toolTipDelayMs
                    ToolTip.timeout: AppConfig.toolTipTimeoutMs

                }

                CustomSpinBox {

                    id: tiltAfterRotationSpinBox
                    Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                    from: MicroscopeBounds.tiltAfterRotationMin
                    to: MicroscopeBounds.tiltAfterRotationMax
                    value: appController.settings.tiltAfterRotationAngleDeg
                    // Write back on user interaction only (typing, keys,
                    // wheel, and the preset label taps below, which emit
                    // valueModified explicitly). onValueChanged would
                    // also fire on the programmatic startup binding
                    // evaluation and echo the setting back to itself.
                    onValueModified: appController.settings.tiltAfterRotationAngleDeg = value
                    editable: true
                    showArrows: true
                    enabled: tiltAfterRotationCheckBox.checked && !root.rotating

                }

                Label {

                    id: seventeenLabel

                    readonly property int seventeenLabelPresetValue: 17

                    text: seventeenLabelPresetValue
                    leftPadding: 5
                    rightPadding: 5
                    color: seventeenLabelHover.hovered ? Universal.accent
                                                       : Universal.foreground
                    opacity: seventeenLabelHover.hovered ? 1.0 : 0.55

                    enabled: tiltAfterRotationSpinBox.enabled

                    background: HoverOutline {
                        hovered: seventeenLabelHover.hovered
                    }

                    HoverHandler {
                        id: seventeenLabelHover
                        cursorShape: Qt.PointingHandCursor
                    }

                    TapHandler {
                        onTapped: {
                            tiltAfterRotationSpinBox.value =
                                seventeenLabel.seventeenLabelPresetValue
                            // A tap is user interaction: route it through
                            // the same write-back path as typing/wheel.
                            tiltAfterRotationSpinBox.valueModified()
                        }
                    }

                    ToolTip.text: Strings.seventeenLabelTooltip
                    ToolTip.visible: seventeenLabelHover.hovered
                    ToolTip.delay: AppConfig.toolTipDelayMs
                    ToolTip.timeout: AppConfig.toolTipTimeoutMs

                }

                Label {

                    id: thirtyFiveLabel

                    readonly property int thirtyFiveLabelPresetValue: 35

                    text: thirtyFiveLabelPresetValue
                    leftPadding: 5
                    rightPadding: 5
                    color: thirtyFiveLabelHover.hovered ? Universal.accent
                                                        : Universal.foreground
                    opacity: thirtyFiveLabelHover.hovered ? 1.0 : 0.55

                    enabled: tiltAfterRotationSpinBox.enabled

                    background: HoverOutline {
                        hovered: thirtyFiveLabelHover.hovered
                    }

                    HoverHandler {
                        id: thirtyFiveLabelHover
                        cursorShape: Qt.PointingHandCursor
                    }

                    TapHandler {
                        onTapped: {
                            tiltAfterRotationSpinBox.value =
                                thirtyFiveLabel.thirtyFiveLabelPresetValue
                            // A tap is user interaction: route it through
                            // the same write-back path as typing/wheel.
                            tiltAfterRotationSpinBox.valueModified()
                        }
                    }

                    ToolTip.text: Strings.thirtyFiveLabelTooltip
                    ToolTip.visible: thirtyFiveLabelHover.hovered
                    ToolTip.delay: AppConfig.toolTipDelayMs
                    ToolTip.timeout: AppConfig.toolTipTimeoutMs

                }

            }

            Item {

                Layout.row: 4
                Layout.columnSpan: 2
                Layout.preferredHeight: AppConfig.stageZSeparatorHeight

            }

            Label {

                id: stageZSliderLabel
                Layout.row: 5
                Layout.columnSpan: 2
                Layout.alignment: Qt.AlignHCenter
                text: "Stage Z (Z-Y Link enabled)"

                ToolTip.text: Strings.stageZSliderLabelTooltip
                ToolTip.visible: stageZSliderLabelHover.hovered
                ToolTip.delay: AppConfig.toolTipDelayMs
                ToolTip.timeout: AppConfig.toolTipTimeoutMs

                HoverHandler { id: stageZSliderLabelHover }

            }

            Item {

                id: stageZSliderCell
                Layout.row: 6
                Layout.columnSpan: 2
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: parent.width * 0.75
                // Normal height when usable; grows to fit the lock prompt
                // only while blocked.
                implicitHeight: root.zSafetyState === "BLOCKED"
                                ? Math.max(stageZSlider.implicitHeight,
                                           zSafetyOverlay.implicitHeight)
                                : stageZSlider.implicitHeight

                Slider {

                    id: stageZSlider
                    anchors.fill: parent
                    from: AppConfig.stageZSliderMin
                    to: AppConfig.stageZSliderMax
                    stepSize: 1
                    snapMode: Slider.SnapAlways
                    // Live only when a rotation isn't running and the gate
                    // has confirmed the slider may move -- i.e. SAFE (in
                    // range) or UNLOCKED (out of range, acknowledged).
                    // BLOCKED and the pre-first-reading UNKNOWN both keep
                    // it fail-closed (disabled).
                    enabled: !root.rotating
                             && (root.zSafetyState === "SAFE"
                                 || root.zSafetyState === "UNLOCKED")
                    onMoved: {
                        if (appController.stageScan) {
                            appController.stageScan.set_z_slider(value)
                            stageZSlider.state = ""
                        }
                    }
                    onPressedChanged: {
                        if (pressed) {
                            // Click-to-position: the user tapped at `value`
                            // without (yet) dragging. Push the value so the
                            // worker handles click-and-release the same way
                            // it handles drag.
                            if (appController.stageScan) {
                                appController.stageScan.set_z_slider(value)
                            }
                        } else {
                            // Release: snap the slider visually back to 0
                            // and tell the worker to stop issuing moves.
                            // The 0 also clears any fail-locked state on
                            // the worker's next tick.
                            value = 0
                            if (appController.stageScan) {
                                appController.stageScan.set_z_slider(0)
                            }
                        }
                    }
                    // The slider background and groove is designed to be filled from the center.
                    // The user moves the slider from the slider and the color should change
                    // from the center to the slider handle.
                    background:
                        Rectangle {
                        id: groove
                        anchors.verticalCenter: stageZSlider.verticalCenter
                        anchors.left: stageZSlider.left
                        anchors.leftMargin: stageZSlider.handle.implicitWidth
                        anchors.right: stageZSlider.right
                        anchors.rightMargin: stageZSlider.handle.implicitWidth
                        color: Universal.listMediumColor
                        height: 2.25
                        radius: 1
                    }
                    Rectangle {
                        id: rightDrag
                        anchors.verticalCenter: groove.verticalCenter
                        anchors.left: groove.horizontalCenter
                        anchors.right: stageZSlider.handle.horizontalCenter
                        height: 2.25
                        color: Universal.accent
                    }
                    Rectangle {
                        id: leftDrag
                        anchors.verticalCenter: groove.verticalCenter
                        anchors.right: groove.horizontalCenter
                        anchors.left: stageZSlider.handle.horizontalCenter
                        height: 2.25
                        color: Universal.accent
                    }
                }

                // Lock overlay — a sibling of the slider (not a child),
                // so its Unlock button stays interactive while the slider
                // itself is disabled. Shown only when the gate is BLOCKED
                // (stage out of safe range, not yet acknowledged).
                Rectangle {

                    id: zSafetyOverlay
                    anchors.centerIn: parent
                    width: parent.width
                    height: implicitHeight
                    implicitHeight: zSafetyLockContent.implicitHeight
                                    + 2 * AppConfig.activityContainerPadding
                    visible: root.zSafetyState === "BLOCKED"
                    color: AppConfig.activityContainerBackground
                    border.color: AppConfig.activityContainerIdleBorder
                    border.width: AppConfig.activityContainerBorderWidth
                    radius: AppConfig.activityContainerRadius

                    ColumnLayout {

                        id: zSafetyLockContent
                        anchors.fill: parent
                        anchors.margins: AppConfig.activityContainerPadding
                        spacing: AppConfig.activityContainerSpacing

                        // Warning row — exception-colored icon + bold title +
                        // distance message: the same check-item vocabulary the
                        // rotate acknowledge dialog uses, kept inline (rounded
                        // card, centered Unlock button below).
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: AppConfig.activityIconTitleGap

                            Item {
                                Layout.preferredWidth: AppConfig.activityStatusIconSize
                                Layout.preferredHeight: AppConfig.activityStatusIconSize
                                Layout.alignment: Qt.AlignTop

                                Image {
                                    id: zSafetyIcon
                                    anchors.fill: parent
                                    source: AppConfig.iconWarning
                                    sourceSize.width: width * 2
                                    sourceSize.height: height * 2
                                    fillMode: Image.PreserveAspectFit
                                    visible: false
                                }
                                ColorOverlayEffect {
                                    anchors.fill: zSafetyIcon
                                    source: zSafetyIcon
                                    colorOverlayColor: AppConfig.activityExceptionColor
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2

                                Label {
                                    text: Strings.stageZSafetyTitle
                                    font.bold: true
                                    font.pixelSize: AppConfig.pageBodyFontSize
                                    color: AppConfig.universalForeground
                                    wrapMode: Label.WordWrap
                                    Layout.fillWidth: true
                                }
                                Label {
                                    text: root.zSafetyMessage !== ""
                                              ? root.zSafetyMessage
                                              : Strings.stageZSafetyOutOfRangeFallback
                                    font.pixelSize: AppConfig.pageBodyFontSize
                                    color: AppConfig.universalForeground
                                    wrapMode: Label.WordWrap
                                    Layout.fillWidth: true
                                }
                            }
                        }

                        RoundButton {

                            id: zUnlockButton
                            text: Strings.stageZSafetyUnlockButton
                            Layout.alignment: Qt.AlignHCenter
                            radius: AppConfig.buttonRadius
                            onClicked: if (appController.stageScan)
                                           appController.stageScan.unlockZSlider()
                        }
                    }
                }
            }

        }

        Item { Layout.fillHeight: true }

    }

}
