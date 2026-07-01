
pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import "../Config"
import "../Js"

// Shuttle Reference Graphics (static layer)
//
// Draws the fixed reference geometry for the shuttle diagram on the
// Milling Angle Calc page:
// * SEM beam line (vertical) and FIB beam line (38 deg screen angle,
//   right of the SEM) - equal lengths
// * GIS line (16.5 deg screen angle) - short, arctis-style
// * Horizontal stage-plane reference lines (tilt 0) with the "0" label
//   at the right
// * The tilt gauge: a protractor of the stage plane itself.
//   - Right arc, screen 0..+10: path of the stage plane's RIGHT arm
//     for tilts 0..-10 (negative tilt raises the FIB side)
//   - Left arc, screen 120..180: path of the stage plane's LEFT arm
//     for tilts 0..+60
//   Tick marks for negative tilt values sit on the right arc, positive
//   values on the left arc (see tiltTickScreenAngleDeg()).
//
// Clickable tick labels emit stageTiltClicked(value); the page routes
// this to MillingAngleCalculator.animateStageTiltTo(). The SEM/FIB/GIS
// beam labels emit beamClicked(name); the page applies the matching
// stage preset (rotation regime + tilt) from
// MillingAngleCalculations.beamPreset().
//
// The figure has TWO vertical reference heights:
// * centerY - the shuttle fiducial (rotation point); the SEM/FIB/GIS
//   beam lines radiate from here.
// * gaugeCenterY - the shuttle base height at tilt 0; the tilt gauge
//   (arcs, ticks, horizontals) is centered here so the 0-degree line is
//   collinear with the shuttle base. Note the gauge center is below the
//   rotation point: at nonzero tilt the base remains PARALLEL to the
//   indicated direction but does not pass through the gauge center.
//
// All instrument angles come from MillingAngleCalculations; this file
// contains layout only.

Item {

    id: root

    // Figure center (the shuttle fiducial / rotation point) in this
    // item's coordinates. Exposed so sibling layers share one origin.
    property real centerX: width / 2
    // Fiducial height: derived from the topmost content - whichever
    // reaches higher of (a) the beam lines above the fiducial, or (b) the
    // gauge arc's apex (at the max-tilt tick angle) above the fiducial.
    property real centerY: _labelClearance
                           + Math.max(_beamAboveFiducial, _arcAboveFiducial)

    // Tilt gauge center: the shuttle base height at tilt 0.
    property real gaugeCenterY:
        centerY + AppConfig.millingFigureShuttleImageHeight
                  * (1 - AppConfig.millingFigureFiducialFracY)

    signal stageTiltClicked(real tiltValue)
    signal beamClicked(string beamName)

    // Clickable tilt tick values. Negative ticks land on the right arc,
    // positive on the left arc. 0 is labeled at the right horizontal.
    readonly property var tiltTickValues: [-10, -5, 0, 7, 17, 35, 52, 60]

    readonly property real _labelClearance: 36

    readonly property real _beamAboveFiducial:
        AppConfig.millingFigureBeamLabelRadius

    readonly property real _arcAboveFiducial:
        AppConfig.millingFigureArcRadius
            * Math.sin(MillingAngleCalculations.tiltTickScreenAngleDeg(
                           AppConfig.stageTiltAngleMax) * Math.PI / 180)
        + AppConfig.millingFigureTickLength
        - AppConfig.millingFigureShuttleImageHeight
            * (1 - AppConfig.millingFigureFiducialFracY)

    // Tick marks as {value, angle} pairs. Tilt 0 is marked on BOTH arms
    // of the stage plane (right and left), each labeled "0"; all other
    // values appear once, on the arm given by tiltTickScreenAngleDeg().
    readonly property var _tickMarks: {
        var marks = []
        for (var i = 0; i < tiltTickValues.length; i++) {
            marks.push({
                value: tiltTickValues[i],
                angle: MillingAngleCalculations.tiltTickScreenAngleDeg(
                           tiltTickValues[i])
            })
        }
        marks.push({
            value: 0,
            angle: MillingAngleCalculations.stagePlaneLeftArmScreenAngleDeg(0)
        })
        return marks
    }

    implicitWidth: 2 * (AppConfig.millingFigureArcRadius
                        + AppConfig.millingFigureTickLength
                        + _labelClearance)
    implicitHeight: Math.max(
        centerY + AppConfig.millingFigureBottomClearance,
        gaugeCenterY + _labelClearance)

    Canvas {

        id: staticCanvas
        anchors.fill: parent

        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)

            var cx = root.centerX
            var cy = root.centerY
            var arcR = AppConfig.millingFigureArcRadius
            var beamLen = AppConfig.millingFigureBeamLineLength
            var lineW = AppConfig.millingFigureLineWidth
            var lineColor = AppConfig.millingFigureLineColor
            var beamColor = AppConfig.millingFigureBeamLineColor

            // --- Beam lines ---
            // Each line grows INWARD from the shared label arc: outer
            // end at (labelRadius - labelOffset), reaching toward the
            // center by its own length. SEM and FIB are equal length;
            // GIS is a shorter stub, but its label stays on the arc.
            var lineOuter = AppConfig.millingFigureBeamLabelRadius
                            - AppConfig.millingFigureBeamLabelOffset
            var gisLen = beamLen * AppConfig.millingFigureGisLineFraction
            DiagramFunctions.drawRadialLine(
                ctx, cx, cy, lineOuter - beamLen, beamLen,
                MillingAngleCalculations.semScreenAngleDeg(),
                beamColor, lineW)
            DiagramFunctions.drawRadialLine(
                ctx, cx, cy, lineOuter - beamLen, beamLen,
                MillingAngleCalculations.fibScreenAngleDeg(),
                beamColor, lineW)
            DiagramFunctions.drawRadialLine(
                ctx, cx, cy, lineOuter - gisLen, gisLen,
                MillingAngleCalculations.gisScreenAngleDeg(),
                beamColor, lineW)

            var gy = root.gaugeCenterY

            // --- Tilt gauge arcs (reachable stage tilt range only) ---
            // Right arc: tilts 0..-10 (right arm path).
            DiagramFunctions.drawArc(
                ctx, cx, gy, arcR,
                MillingAngleCalculations.tiltTickScreenAngleDeg(0),
                MillingAngleCalculations.tiltTickScreenAngleDeg(
                    AppConfig.stageTiltAngleMin),
                lineColor, lineW)
            // Left arc: tilts +60..0 (left arm path).
            DiagramFunctions.drawArc(
                ctx, cx, gy, arcR,
                MillingAngleCalculations.tiltTickScreenAngleDeg(
                    AppConfig.stageTiltAngleMax),
                MillingAngleCalculations.stagePlaneLeftArmScreenAngleDeg(0),
                lineColor, lineW)

            // --- Tick marks (tilt 0 marked on both arms) ---
            for (var i = 0; i < root._tickMarks.length; i++) {
                DiagramFunctions.drawRadialLine(
                    ctx, cx, gy, arcR,
                    AppConfig.millingFigureTickLength,
                    root._tickMarks[i].angle,
                    lineColor, lineW)
            }
        }

    }

    // ----------------------------------------------------------------
    // Beam identifier labels
    // ----------------------------------------------------------------

    Repeater {

        model: [
            { name: "SEM",
              angle: MillingAngleCalculations.semScreenAngleDeg() },
            { name: "FIB",
              angle: MillingAngleCalculations.fibScreenAngleDeg() },
            { name: "GIS",
              angle: MillingAngleCalculations.gisScreenAngleDeg() }
        ]

        delegate: Label {

            id: beamLabel

            required property var modelData

            // All beam labels sit on one common arc.
            readonly property var _pos:
                DiagramFunctions.getRadialLabelPosition(
                    root.centerX, root.centerY,
                    AppConfig.millingFigureBeamLabelRadius,
                    0, modelData.angle, 0)

            x: _pos.x - width / 2 + (width / 2 + 6)
               * Math.cos(modelData.angle * Math.PI / 180)
            y: _pos.y - height / 2 - (height / 2 + 2)
               * Math.sin(modelData.angle * Math.PI / 180)
            text: modelData.name
            font.pixelSize: AppConfig.millingFigureLabelFontSize
            font.bold: false
            color: beamLabelMA.containsMouse
                   ? AppConfig.universalAccent
                   : AppConfig.millingFigureBeamLineColor

            background: HoverOutline {
                hovered: beamLabelMA.containsMouse
            }

            MouseArea {
                id: beamLabelMA
                anchors.fill: parent
                anchors.margins: -6
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.beamClicked(beamLabel.modelData.name)
            }

        }

    }

    // ----------------------------------------------------------------
    // Clickable tilt tick labels
    // ----------------------------------------------------------------

    Repeater {

        model: root._tickMarks

        delegate: Label {

            id: tickLabel

            required property var modelData

            readonly property real _tickAngle: modelData.angle
            readonly property var _pos:
                DiagramFunctions.getRadialLabelPosition(
                    root.centerX, root.gaugeCenterY,
                    AppConfig.millingFigureArcRadius,
                    AppConfig.millingFigureTickLength,
                    _tickAngle, AppConfig.millingFigureLabelOffset)

            x: _pos.x - width / 2 + (width / 2 + 4)
               * Math.cos(_tickAngle * Math.PI / 180)
            y: _pos.y - height / 2 - (height / 2 + 2)
               * Math.sin(_tickAngle * Math.PI / 180)
            text: modelData.value
            font.pixelSize: AppConfig.millingFigureLabelFontSize
            color: tickLabelMA.containsMouse
                   ? AppConfig.universalAccent
                   : AppConfig.millingFigureLineColor

            background: HoverOutline {
                hovered: tickLabelMA.containsMouse
            }

            MouseArea {
                id: tickLabelMA
                anchors.fill: parent
                anchors.margins: -6
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.stageTiltClicked(tickLabel.modelData.value)
            }

        }

    }

}

