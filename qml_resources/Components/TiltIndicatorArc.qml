import QtQuick
import "../Config"
import "../Js"

// Tilt Indicator Arc
//
// The green gauge overlay: an arc along the tilt gauge from the tilt-0
// position to the stage plane arm's current position (left arm for
// positive tilt, right arm for negative). Stacked ABOVE the reference
// graphics in ShuttleDiagram so it overlays the gauge arc line; the
// rest of the tilt-dependent drawing (shuttle image, stage-plane guide)
// lives in ShuttleGraphics, below the reference layer.

Item {

    id: root

    property real stageTiltAngle: 0

    // Gauge center; bound by the parent diagram to match the reference
    // layer.
    property real centerX: width / 2
    property real gaugeCenterY: height / 2

    Canvas {

        id: indicatorCanvas
        anchors.fill: parent

        property real currentTilt: root.stageTiltAngle
        onCurrentTiltChanged: requestPaint()

        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)

            var t = currentTilt
            if (Math.abs(t) < 0.05)
                return  // At tilt 0 the indicator collapses to nothing.

            // Positive tilt: left arm sweeps up from 180; negative tilt:
            // right arm sweeps up from 0. drawArc runs CCW from start to
            // end, so order the bounds ascending.
            var startDeg
            var endDeg
            if (t > 0) {
                startDeg = MillingAngleCalculations
                    .stagePlaneLeftArmScreenAngleDeg(t)
                endDeg = MillingAngleCalculations
                    .stagePlaneLeftArmScreenAngleDeg(0)
            } else {
                startDeg = MillingAngleCalculations
                    .stagePlaneRightArmScreenAngleDeg(0)
                endDeg = MillingAngleCalculations
                    .stagePlaneRightArmScreenAngleDeg(t)
            }

            DiagramFunctions.drawArc(
                ctx, root.centerX, root.gaugeCenterY,
                AppConfig.millingFigureArcRadius,
                startDeg, endDeg,
                AppConfig.millingFigureIndicatorColor,
                AppConfig.millingFigureIndicatorWidth)
        }

    }

}
