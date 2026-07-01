import QtQuick
import "../Config"
import "../Js"

// Shuttle Graphics (dynamic layer)
//
// Draws the tilt-dependent elements that belong BELOW the reference
// graphics (the tilt indicator arc, which must overlay the gauge line,
// lives in TiltIndicatorArc and is stacked above the reference layer
// by ShuttleDiagram):
// * The shuttle SVG, rotated about its grid fiducial point. The SVG
//   source switches with the stage rotation regime (-70 / 110); the two
//   assets are mirror-image side views of the 35 degree pre-tilt shuttle.
//   The regime switch is animated as a horizontal squash through zero
//   about the fiducial's vertical axis: the orthographic side-view
//   projection of the real 180-degree stage rotation about its normal
//   is exactly this squash, so the flip reads as the actual motion.
// * The stage-plane guide: a faint diameter line through the gauge
//   center at the current plane angle. The shuttle rotates about its
//   grid fiducial (above the gauge center), so at nonzero tilt the
//   shuttle base is parallel to, but offset from, the center-to-tick
//   direction; the guide passes exactly through the gauge center and
//   the ticks, bridging that gap visually.
//
// The fiducial fractions come from AppConfig (measured from the fiducial
// circles embedded in the SVG sources).
//
// Positive stage tilt rotates the shuttle clockwise on screen
// (see millingAngleCalculations.js).

Item {

    id: root

    property real stageTiltAngle: 0
    property int rotationRegime: MillingAngleCalculations.ROTATION_NEG_70

    // Figure center (the rotation point); bound by the parent diagram to
    // match the reference layer's center.
    property real centerX: width / 2
    property real centerY: height / 2

    // Vertical center of the tilt gauge (the shuttle base height at
    // tilt 0); bound by the parent diagram to match the reference layer.
    property real gaugeCenterY: centerY

    // The displayed regime lags the bound rotationRegime during the
    // flip animation: squash the current image to zero width, swap the
    // mirrored SVG, expand back.
    property int _displayedRegime: MillingAngleCalculations.ROTATION_NEG_70

    readonly property bool _isNeg70:
        _displayedRegime === MillingAngleCalculations.ROTATION_NEG_70

    Component.onCompleted: _displayedRegime = rotationRegime

    onRotationRegimeChanged: regimeFlipAnimation.restart()

    SequentialAnimation {
        id: regimeFlipAnimation
        NumberAnimation {
            target: flipScale
            property: "xScale"
            to: 0
            duration: AppConfig.millingFigureFlipHalfDurationMs
            easing.type: Easing.InQuad
        }
        ScriptAction {
            script: root._displayedRegime = root.rotationRegime
        }
        NumberAnimation {
            target: flipScale
            property: "xScale"
            to: 1
            duration: AppConfig.millingFigureFlipHalfDurationMs
            easing.type: Easing.OutQuad
        }
    }

    // Fiducial position as fractions of the image size (see AppConfig).
    readonly property real _fiducialFracX:
        _isNeg70 ? AppConfig.millingFigureFiducialFracXNeg70
                 : AppConfig.millingFigureFiducialFracX110
    readonly property real _fiducialFracY:
        AppConfig.millingFigureFiducialFracY

    // SVG viewBox aspect ratio. Computed by
    // tools/measure_shuttle_fiducial.py - re-run after editing the SVGs.
    readonly property real _imageAspect: 1.94233

    // ----------------------------------------------------------------
    // Stage-plane guide
    // ----------------------------------------------------------------

    Canvas {

        id: guideCanvas
        anchors.fill: parent
        visible: AppConfig.millingFigureStagePlaneGuideVisible

        // Repaint whenever the tilt changes.
        property real currentTilt: root.stageTiltAngle
        onCurrentTiltChanged: requestPaint()

        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)

            var t = currentTilt

            // Stage-plane guide: faint radius from the gauge center to
            // the active arm's gauge position (left arm for positive
            // tilt, right arm for negative; both at tilt 0, where the
            // guide doubles as a floor line under the shuttle base).
            var guideColor = Qt.alpha(AppConfig.millingFigureLineColor, 0.35)
            if (t >= 0) {
                DiagramFunctions.drawRadialLine(
                    ctx, root.centerX, root.gaugeCenterY, 0,
                    AppConfig.millingFigureArcRadius,
                    MillingAngleCalculations.stagePlaneLeftArmScreenAngleDeg(t),
                    guideColor, AppConfig.millingFigureLineWidth)
            }
            if (t <= 0) {
                DiagramFunctions.drawRadialLine(
                    ctx, root.centerX, root.gaugeCenterY, 0,
                    AppConfig.millingFigureArcRadius,
                    MillingAngleCalculations.stagePlaneRightArmScreenAngleDeg(t),
                    guideColor, AppConfig.millingFigureLineWidth)
            }
        }

    }

    // ----------------------------------------------------------------
    // Shuttle image, rotating about the fiducial
    // ----------------------------------------------------------------

    Image {

        id: shuttleImage

        readonly property real fidX: width * root._fiducialFracX
        readonly property real fidY: height * root._fiducialFracY

        height: AppConfig.millingFigureShuttleImageHeight
        width: height * root._imageAspect
        sourceSize.width: width * 2   // 2x raster for crisp scaling
        sourceSize.height: height * 2
        smooth: true

        // Place the fiducial on the figure center.
        x: root.centerX - fidX
        y: root.centerY - fidY

        source: root._isNeg70
                ? "../assets/35-shuttle_rot_neg70.svg"
                : "../assets/35-shuttle_rot_110.svg"

        // Rotation is about the grid fiducial (eucentric): the sample
        // stays pinned at the SEM/FIB/GIS convergence at all tilts,
        // matching real stage behavior. A base-biased rotation point
        // was explored and rejected - it makes the base track the gauge
        // ticks exactly but lets the grid drift away from the beams at
        // high tilt, breaking the beam-sample relationship the figure
        // exists to illustrate. The stage-plane guide line bridges the
        // base/tick parallax instead.
        // Transform order: the flip squash applies first (about the
        // fiducial's vertical axis, i.e. the stage rotation axis), then
        // the tilt rotation about the fiducial.
        transform: [
            Scale {
                id: flipScale
                origin.x: shuttleImage.fidX
                origin.y: shuttleImage.fidY
                xScale: 1
            },
            Rotation {
                origin.x: shuttleImage.fidX
                origin.y: shuttleImage.fidY
                angle: MillingAngleCalculations.shuttleItemRotation(
                           root.stageTiltAngle)
            }
        ]

    }

}
