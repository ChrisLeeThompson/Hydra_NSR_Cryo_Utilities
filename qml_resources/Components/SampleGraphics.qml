pragma ComponentBehavior: Bound

import QtQuick
import "../Config"
import "../Js"

// Sample Graphics (rotating layer)
//
// Cartoon cross-section of the sample as it sits on the shuttle: a
// thicker filled rectangle (the sample) resting on a thin outlined
// rectangle (the grid). Both rotate with the pre-tilted shuttle face,
// so the figure's angles match the shuttle diagram, including the
// rotation regime.
//
// Chalk lines represent FIB cuts. They are stored (owned by
// SampleDiagram) as { localAngleDeg, creationRegime }: the LOCAL angle
// in the sample-plane frame (by the milling angle identity, the milling
// angle the line was cut at) plus the regime it was cut in. When shown
// in the other regime the angle is mirrored (the 180-degree stage
// rotation reflects the side view - effectiveLocalAngleDeg() in
// millingAngleCalculations.js). Lines pass through the figure center
// and are clipped to the union of the sample and grid rectangles.
// Lines are recolored when they are currently perpendicular or
// parallel to any beam.
//
// An optional preview line (shown while the user hovers the "Add chalk
// line" button) draws faintly along the FIB direction, showing where
// the next cut would land.
//
// The regime switch is animated as a squash through zero along the
// sample plane's local direction (the orthographic side-view of the
// real 180-degree stage rotation about its normal), mirroring the
// shuttle figure's flip and sharing its half-duration constant.

Item {

    id: root

    property real stageTiltAngle: 0
    property int rotationRegime: MillingAngleCalculations.ROTATION_NEG_70

    // Chalk lines ({ localAngleDeg, creationRegime }); owned by
    // SampleDiagram.
    property var chalkLines: []

    // Faint preview of the next chalk line (along the FIB direction).
    property bool previewVisible: false

    property real centerX: width / 2
    property real centerY: height / 2

    // The displayed regime lags the bound rotationRegime during the
    // flip animation: squash the plane to zero width, swap the regime,
    // expand back.
    property int _displayedRegime: MillingAngleCalculations.ROTATION_NEG_70

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

    // The plane item rotates so a local angle L appears on screen at
    // L + sampleFaceScreenAngleDeg (QML rotation is clockwise-positive).
    readonly property real _planeRotation:
        -MillingAngleCalculations.sampleFaceScreenAngleDeg(
            stageTiltAngle, _displayedRegime)

    Canvas {

        id: canvas
        anchors.fill: parent
        // Transform order: the flip squash applies first (along the
        // plane's local direction), then the face rotation. Both pivot
        // at the FIGURE center (centerX, centerY), not the item center:
        // the figure center sits above the item's midpoint (the bottom
        // half only needs to clear the swept rectangles), so the
        // default origin would make the sample orbit instead of
        // spinning in place.
        transform: [
            Scale {
                id: flipScale
                origin.x: root.centerX
                origin.y: root.centerY
                xScale: 1
            },
            Rotation {
                origin.x: root.centerX
                origin.y: root.centerY
                angle: root._planeRotation
            }
        ]

        smooth: true

        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)

            var cx = root.centerX
            var cy = root.centerY
            var sw = AppConfig.sampleFigureSampleRectWidth
            var sh = AppConfig.sampleFigureSampleRectHeight
            var gw = AppConfig.sampleFigureGridRectWidth
            var gh = AppConfig.sampleFigureGridRectHeight
            // The grid sits directly below the sample.
            var gridCenterY = cy + sh / 2 + gh / 2

            // --- Sample rectangle (filled, outlined) ---
            ctx.fillStyle = AppConfig.sampleFigureSampleFillColor
            ctx.fillRect(cx - sw / 2, cy - sh / 2, sw, sh)
            ctx.strokeStyle = AppConfig.millingFigureLineColor
            ctx.lineWidth = AppConfig.millingFigureLineWidth
            ctx.strokeRect(cx - sw / 2, cy - sh / 2, sw, sh)

            // --- Grid rectangle (thin outline) ---
            ctx.strokeRect(cx - gw / 2, gridCenterY - gh / 2, gw, gh)

            // --- Chalk lines ---
            // Drawn at the effective local angle for the DISPLAYED
            // regime so lines mirror together with the plane through
            // the flip animation.
            ctx.lineWidth = AppConfig.sampleFigureChalkLineWidth
            for (var i = 0; i < root.chalkLines.length; i++) {
                var line = root.chalkLines[i]
                ctx.strokeStyle = root._chalkColor(line)
                root._strokeClippedLine(
                    ctx,
                    MillingAngleCalculations.effectiveLocalAngleDeg(
                        line.localAngleDeg, line.creationRegime,
                        root._displayedRegime),
                    1.0)
            }

            // --- Preview of the next cut (along the FIB) ---
            if (root.previewVisible) {
                ctx.lineWidth = AppConfig.sampleFigureChalkLineWidth
                ctx.strokeStyle = Qt.alpha(
                    AppConfig.sampleFigureChalkLineColor, 0.35)
                root._strokeClippedLine(
                    ctx,
                    MillingAngleCalculations.chalkLineLocalAngleDeg(
                        root.stageTiltAngle, root._displayedRegime),
                    1.0)
            }
        }

    }

    // Color for a chalk line based on its current relation to any beam:
    // perpendicular wins over parallel wins over the default color.
    // Uses the canonical rotationRegime (not _displayedRegime) so the
    // highlight reports the true physical relation.
    function _chalkColor(line) {
        var beams = ["SEM", "FIB", "GIS"]
        var parallel = false
        for (var i = 0; i < beams.length; i++) {
            var rel = MillingAngleCalculations.chalkLineRelation(
                line.localAngleDeg, line.creationRegime,
                stageTiltAngle, rotationRegime, beams[i])
            if (rel === MillingAngleCalculations.RELATION_PERPENDICULAR)
                return AppConfig.perpendicularHighlightColor
            if (rel === MillingAngleCalculations.RELATION_PARALLEL)
                parallel = true
        }
        return parallel ? AppConfig.parallelHighlightColor
                        : AppConfig.sampleFigureChalkLineColor
    }

    // Stroke a line through the figure center at the given LOCAL angle,
    // clipped to the union of the sample and grid rectangles (the two
    // most distant intersection points, as in the arctis calculator).
    // Drawing happens in the rotating canvas, so local angles are used
    // directly; the canvas y-axis points down, so a CCW local angle is
    // drawn with a negated slope.
    function _strokeClippedLine(ctx, localAngleDeg, alpha) {
        var cx = centerX
        var cy = centerY
        var sh = AppConfig.sampleFigureSampleRectHeight
        var gh = AppConfig.sampleFigureGridRectHeight
        var pts = []
        pts = pts.concat(DiagramFunctions.lineRotatedRectangleIntersection(
            cx, cy, localAngleDeg, cx, cy,
            AppConfig.sampleFigureSampleRectWidth, sh, 0))
        pts = pts.concat(DiagramFunctions.lineRotatedRectangleIntersection(
            cx, cy, localAngleDeg, cx, cy + sh / 2 + gh / 2,
            AppConfig.sampleFigureGridRectWidth, gh, 0))
        if (pts.length < 2)
            return
        var best = [0, 1]
        var maxDist = -1
        for (var i = 0; i < pts.length; i++) {
            for (var j = i + 1; j < pts.length; j++) {
                var dx = pts[j].x - pts[i].x
                var dy = pts[j].y - pts[i].y
                var d = dx * dx + dy * dy
                if (d > maxDist) {
                    maxDist = d
                    best = [i, j]
                }
            }
        }
        ctx.beginPath()
        ctx.moveTo(pts[best[0]].x, pts[best[0]].y)
        ctx.lineTo(pts[best[1]].x, pts[best[1]].y)
        ctx.stroke()
    }

    onChalkLinesChanged: canvas.requestPaint()
    onStageTiltAngleChanged: canvas.requestPaint()
    on_DisplayedRegimeChanged: canvas.requestPaint()
    onPreviewVisibleChanged: canvas.requestPaint()

}
