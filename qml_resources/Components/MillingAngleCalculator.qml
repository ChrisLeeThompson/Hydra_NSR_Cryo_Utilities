
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../Config"
import "../Components"
import "../Js"

// Milling Angle Calculator
//
// Assists the user with calculating the milling angle
// as it relates to the stage tilt angle.
// Calculations are based on the 35 degree pre-tilt AutoGrid Shuttle.
// All geometry lives in Js/millingAngleCalculations.js.
//
// The calculator includes:
// * Milling angle SpinBox
// * Stage tilt angle SpinBox
// * Buttons to toggle the calculations between -70 and 110 degree stage
//   rotation positions (radio-style: exactly one is always checked).
// The shuttle and sample figures will rotate depending on the milling angle
// and stage tilt angle values.
//
// State model:
// * stageTiltAngle is the single canonical stored value (the physical
//   quantity). The milling angle is always derived from it.
// * Editing the milling angle SpinBox converts the entered value to a
//   stage tilt and stores that.
// * Switching rotation regime preserves the stage tilt (the stage does
//   not tilt when it rotates between positions) and the displayed milling
//   angle recomputes through the bindings.
// * SpinBox write-back uses onValueModified (user interaction only) so
//   programmatic floatValue updates can never cause binding loops.
//
// Public contract (what the shuttle/sample figure components bind to):
// * stageTiltAngle (real, degrees)
// * millingAngle   (real, degrees, readonly, derived)
// * rotationRegime (int, MillingAngleCalculations.ROTATION_NEG_70 or
//                   MillingAngleCalculations.ROTATION_110, readonly,
//                   derived from the checked rotation button)

GroupBox {

    id: root

    // ------------------------------------------------------------------
    // Public state
    // ------------------------------------------------------------------

    // Canonical stored value. Everything else is derived.
    property real stageTiltAngle: AppConfig.stageTiltAngleDefault

    // Current stage rotation regime. The checked button is the source of
    // truth; autoExclusive guarantees exactly one is checked at all times.
    readonly property int rotationRegime:
        oneTenRotateButton.checked
            ? MillingAngleCalculations.ROTATION_110
            : MillingAngleCalculations.ROTATION_NEG_70

    // Milling angle, always derived from the canonical stage tilt.
    readonly property real millingAngle:
        MillingAngleCalculations.calculateMillingAngle(
            root.stageTiltAngle, root.rotationRegime)

    // Animate the canonical stage tilt to a target value (used by the
    // clickable tilt gauge labels in the diagrams). Everything derived -
    // milling angle, SpinBox displays, shuttle rotation, indicator arc -
    // sweeps along through the bindings.
    function animateStageTiltTo(targetTilt) {
        stageTiltAnimation.to = targetTilt
        stageTiltAnimation.restart()
    }

    // Set the canonical stage tilt immediately, first cancelling any
    // in-flight animateStageTiltTo sweep. Used by the mouse-wheel tilt
    // adjust: a wheel scroll must win over a click-triggered 500 ms
    // stageTiltAnimation, otherwise a scroll within ~0.5 s of a label /
    // chip click would be silently clobbered as the animation finishes.
    function setStageTiltDirect(targetTilt) {
        stageTiltAnimation.stop()
        stageTiltAngle = targetTilt
    }

    // Apply a stage orientation preset (used by the clickable beam
    // labels in the diagrams): switch the rotation regime, then animate
    // the stage tilt to the target. Setting `checked` programmatically
    // respects autoExclusive, so the other regime button unchecks.
    function applyStagePreset(rotationRegimeTarget, stageTiltTarget) {
        if (rotationRegimeTarget === MillingAngleCalculations.ROTATION_110)
            oneTenRotateButton.checked = true
        else
            negativeSeventyRotateButton.checked = true
        animateStageTiltTo(stageTiltTarget)
    }

    // Reachable milling-angle limits for the current regime, derived from
    // the physical stage tilt limits. Because millingAngle is computed
    // from an in-range stage tilt, it always lies within these limits —
    // including at the instant of a regime switch.
    readonly property var _millingAngleRange:
        MillingAngleCalculations.millingAngleRange(
            root.rotationRegime,
            AppConfig.stageTiltAngleMin,
            AppConfig.stageTiltAngleMax)

    Layout.fillWidth: true
    focusPolicy: Qt.StrongFocus

    NumberAnimation {
        id: stageTiltAnimation
        target: root
        property: "stageTiltAngle"
        duration: 500
        easing.type: Easing.InOutQuad
    }

    background: Rectangle {
        anchors.fill: parent
        color: "transparent"
    }

    GridLayout {

        id: millingAngleCalcGrid
        anchors.fill: parent
        rowSpacing: AppConfig.activityFormRowSpacing
        columnSpacing: AppConfig.activityFormColumnSpacing

        ToolTippedLabel {

            id: millingAngleLabel
            Layout.row: 0
            Layout.column: 0
            text: "Milling Angle (deg.)"
            toolTipText: Strings.millingAngleLabelTooltip

        }

        CustomSpinBox {

            id: millingAngleSB
            Layout.row: 0
            Layout.column: 1
            Layout.alignment: Qt.AlignRight
            Layout.preferredWidth: {
                negativeSeventyRotateButton.width + oneTenRotateButton.width
                        + AppConfig.activityFormRowSpacing
            }
            floatFrom: root._millingAngleRange.min
            floatTo: root._millingAngleRange.max
            floatStep: 0.1
            floatValue: root.millingAngle
            decimals: 1
            showArrows: true

            // User edited the milling angle: convert to the canonical
            // stage tilt. realValue is already clamped to floatFrom/To,
            // so the resulting tilt is always within the stage limits.
            onValueModified: {
                root.stageTiltAngle =
                    MillingAngleCalculations.calculateStageTilt(
                        realValue, root.rotationRegime)
            }

        }

        ToolTippedLabel {

            id: stageTiltAngleLabel
            Layout.row: 1
            Layout.column: 0
            text: "Stage Tilt Angle (deg.)"
            toolTipText: Strings.stageTiltAngleLabelTooltip

        }

        CustomSpinBox {

            id: stageTiltAngleSB
            Layout.row: 1
            Layout.column: 1
            Layout.alignment: Qt.AlignRight
            Layout.preferredWidth: {
                negativeSeventyRotateButton.width + oneTenRotateButton.width
                        + AppConfig.activityFormRowSpacing
            }
            floatFrom: AppConfig.stageTiltAngleMin
            floatTo: AppConfig.stageTiltAngleMax
            floatStep: 0.1
            floatValue: root.stageTiltAngle
            decimals: 1
            showArrows: true

            // User edited the stage tilt: store it directly.
            onValueModified: {
                root.stageTiltAngle = realValue
            }

        }

        Item { Layout.row: 2; Layout.preferredHeight: 12 }

        RowLayout {

            Layout.row: 3
            Layout.columnSpan: 2
            spacing: AppConfig.activityFormRowSpacing

            ToolTippedLabel {

                id: stageRotationLabel
                text: "Stage Rotation"
                toolTipText: Strings.stageRotationLabelTooltip

            }

            Item { Layout.fillWidth: true }

            RoundButton {

                id: negativeSeventyRotateButton
                radius: AppConfig.buttonRadius
                text: "-70°"
                checkable: true
                autoExclusive: true
                highlighted: checked
                checked: true

            }

            RoundButton {

                id: oneTenRotateButton
                radius: AppConfig.buttonRadius
                text: "110°"
                checkable: true
                autoExclusive: true
                highlighted: checked

            }

        }

    }

}

