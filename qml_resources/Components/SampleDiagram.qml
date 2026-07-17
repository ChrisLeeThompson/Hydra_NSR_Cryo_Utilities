import QtQuick
import "../Config"
import "../Js"

// Sample Diagram
//
// Composes the chalk-line sample figure for the Milling Angle Calc page
// from two layers sharing one center, stacked bottom to top:
// * SampleGraphics - the rotating sample/grid rectangles, chalk lines,
//   and the next-cut preview
// * SampleReferenceGraphics - static beam stubs and clickable cycling
//   labels
//
// This component owns the chalk line state: a list of
// { localAngleDeg, creationRegime } objects - the LOCAL angle in the
// sample-plane frame (equal to the milling angle the line was cut at)
// tagged with the rotation regime it was cut in, so the line can be
// mirrored when evaluated in the other regime (see the chalk line
// section of millingAngleCalculations.js). Add / remove-last /
// clear-all are driven by the page's buttons.
//
// The figure follows the calculator's canonical stage state through the
// stageTiltAngle / rotationRegime bindings, exactly like ShuttleDiagram;
// label and chip clicks emit stageOrientationRequested (regime + tilt,
// since other-regime chips flip the stage rotation) for the page to
// route into MillingAngleCalculator.applyStagePreset().

Item {

    id: root

    property real stageTiltAngle: 0
    property int rotationRegime: MillingAngleCalculations.ROTATION_NEG_70

    // Faint preview of the next cut while hovering the Add button.
    property alias previewVisible: graphicsLayer.previewVisible

    signal stageOrientationRequested(int rotationRegimeTarget,
                                     real tiltValue)

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

    // --- Chalk line state ---
    property var chalkLines: []
    readonly property int chalkLineCount: chalkLines.length

    // Records the calculator's canonical rotationRegime (not the
    // graphics layer's animation-lagged displayed regime): the local
    // angle is computed with that same regime, so angle and tag stay
    // consistent.
    function addChalkLine() {
        var updated = chalkLines.slice()
        updated.push({
            localAngleDeg: MillingAngleCalculations.chalkLineLocalAngleDeg(
                               stageTiltAngle, rotationRegime),
            creationRegime: rotationRegime
        })
        chalkLines = updated
    }

    function removeLastChalkLine() {
        if (chalkLines.length === 0)
            return
        var updated = chalkLines.slice()
        updated.pop()
        chalkLines = updated
    }

    function clearAllChalkLines() {
        chalkLines = []
    }

    // Exposed for offscreen tests and the page wiring.
    function cycleBeam(beamName) {
        referenceLayer.cycleBeam(beamName)
    }

    function applyBeamEntry(beamName, entryIndex) {
        referenceLayer.applyBeamEntry(beamName, entryIndex)
    }

    // --- Figure geometry ---
    // Width is label-driven; height splits at the figure center: the
    // top half clears the beam labels, the bottom half clears the
    // swept sample/grid rectangles at any rotation (their bounding
    // half-diagonal about the center).
    readonly property real _labelClearance: 36
    readonly property real _sweptRectRadius: {
        var halfW = Math.max(AppConfig.sampleFigureSampleRectWidth,
                             AppConfig.sampleFigureGridRectWidth) / 2
        var below = AppConfig.sampleFigureSampleRectHeight / 2
                    + AppConfig.sampleFigureGridRectHeight
        return Math.sqrt(halfW * halfW + below * below)
    }

    // Width is asymmetric: the left half clears the labels/rectangles,
    // the right half additionally reserves room for the position chips.
    readonly property real _centerX:
        AppConfig.sampleFigureBeamLabelRadius + _labelClearance
    implicitWidth: _centerX + AppConfig.sampleFigureBeamLabelRadius
                   + _labelClearance + AppConfig.sampleFigureChipAllowance
    implicitHeight: AppConfig.sampleFigureBeamLabelRadius + _labelClearance
                    + _sweptRectRadius + 8

    readonly property real _centerY:
        AppConfig.sampleFigureBeamLabelRadius + _labelClearance

    SampleGraphics {

        id: graphicsLayer
        anchors.fill: parent
        centerX: root._centerX
        centerY: root._centerY
        stageTiltAngle: root.stageTiltAngle
        rotationRegime: root.rotationRegime
        chalkLines: root.chalkLines

    }

    SampleReferenceGraphics {

        id: referenceLayer
        anchors.fill: parent
        centerX: root._centerX
        centerY: root._centerY
        stageTiltAngle: root.stageTiltAngle
        rotationRegime: root.rotationRegime
        chalkLines: root.chalkLines
        onStageOrientationRequested: function(rotationRegimeTarget,
                                              tiltValue) {
            root.stageOrientationRequested(rotationRegimeTarget, tiltValue)
        }

    }

}
