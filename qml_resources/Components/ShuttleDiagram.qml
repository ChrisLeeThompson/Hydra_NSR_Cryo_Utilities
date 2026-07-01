
import QtQuick
import "../Config"
import "../Js"

// Shuttle Diagram
//
// Composes the shuttle figure for the Milling Angle Calc page from
// three layers sharing one origin (the shuttle's grid fiducial /
// rotation point), stacked bottom to top:
// * ShuttleGraphics - rotating shuttle SVG and stage-plane guide
// * ShuttleReferenceGraphics - static beams, tilt gauge, clickable
//   tick and beam labels (above the shuttle so labels stay readable)
// * TiltIndicatorArc - the green gauge overlay (above the reference
//   layer so it overlays the gauge arc line)
//
// Consumes the MillingAngleCalculator contract (stageTiltAngle,
// rotationRegime) and re-emits tick clicks for the page to route to
// MillingAngleCalculator.animateStageTiltTo().

Item {

    id: root

    property real stageTiltAngle: 0
    property int rotationRegime: MillingAngleCalculations.ROTATION_NEG_70

    signal stageTiltClicked(real tiltValue)
    signal beamClicked(string beamName)

    // ----------------------------------------------------------------
    // Scroll-wheel tilt adjustment
    // ----------------------------------------------------------------
    // Wheel over the figure steps the stage tilt directly (1 degree per
    // notch, clamped to the stage limits) via stageTiltAdjusted - the
    // page applies it WITHOUT animation, so the figure tracks the wheel
    // snappily. angleDelta accumulates into standard 120-unit notches,
    // which coalesces high-resolution trackpad event floods into clean
    // steps. NOTE: this consumes plain wheel events over the figure, so
    // the page ScrollView only scrolls when the cursor is outside the
    // figures; to require Ctrl+wheel instead, set
    // acceptedModifiers: Qt.ControlModifier on the handler.

    signal stageTiltAdjusted(real tiltValue)

    property real _wheelAccumulator: 0

    WheelHandler {
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
        onWheel: function(event) {
            root._wheelAccumulator += event.angleDelta.y
            var steps = Math.trunc(root._wheelAccumulator / 120)
            if (steps === 0)
                return
            root._wheelAccumulator -= steps * 120
            var target = Math.min(AppConfig.stageTiltAngleMax,
                          Math.max(AppConfig.stageTiltAngleMin,
                                   root.stageTiltAngle + steps))
            if (target !== root.stageTiltAngle)
                root.stageTiltAdjusted(target)
        }
    }

    implicitWidth: referenceLayer.implicitWidth
    implicitHeight: referenceLayer.implicitHeight

    // The dynamic layer (shuttle image, indicator arc) sits BELOW the
    // reference layer so the gauge arcs, ticks, and labels stay readable
    // when the rotated shuttle sweeps through the gauge region at high
    // tilt angles. Both layers are line art, so the overlap is benign.
    ShuttleGraphics {

        id: dynamicLayer
        anchors.fill: parent
        centerX: referenceLayer.centerX
        centerY: referenceLayer.centerY
        gaugeCenterY: referenceLayer.gaugeCenterY
        stageTiltAngle: root.stageTiltAngle
        rotationRegime: root.rotationRegime

    }

    ShuttleReferenceGraphics {

        id: referenceLayer
        anchors.fill: parent
        onStageTiltClicked: function(tiltValue) {
            root.stageTiltClicked(tiltValue)
        }
        onBeamClicked: function(beamName) {
            root.beamClicked(beamName)
        }

    }

    TiltIndicatorArc {

        id: indicatorLayer
        anchors.fill: parent
        centerX: referenceLayer.centerX
        gaugeCenterY: referenceLayer.gaugeCenterY
        stageTiltAngle: root.stageTiltAngle

    }

}

