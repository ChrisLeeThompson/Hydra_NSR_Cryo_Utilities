pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import "../Config"
import "../Js"

// Sample Reference Graphics (static layer)
//
// Beam stubs and clickable SEM/FIB/GIS labels for the sample figure,
// using the same label-anchored beam model as the shuttle figure: all
// labels sit on one common arc at sampleFigureBeamLabelRadius, and each
// line grows inward from (labelRadius - beamLabelOffset) by its length.
//
// Label behavior:
// * Clicking the label cycles the stage tilt through the achievable
//   perpendicular/parallel positions for that beam in the CURRENT
//   rotation regime, wrapping around.
// * Numbered chips next to each label list every achievable position
//   in BOTH rotation regimes, in a FIXED canonical order (-70 regime
//   first, then 110, each sorted by tilt) so chip numbers are stable
//   identities for positions; regime flips never reorder them.
//   Other-regime chips are dimmed - clicking those flips the stage
//   rotation as well.
//   Each chip's border is tinted by its relation - mint for
//   perpendicular, pink for parallel - so the relation reads at a
//   glance without selecting or hovering. The border is dim at rest
//   and brightens, with a translucent relation-colored fill, when the
//   stage is currently AT that position (the chip's "active" state);
//   the number stays neutral at rest and takes the relation color when
//   active. Hovering a chip shows a tooltip with the relation, milling
//   angle, stage tilt, and stage rotation for that position.
//   Lists come from MillingAngleCalculations.achievableTiltsForBeam(),
//   called once per regime; per-beam cycle indices reset whenever the
//   chalk lines change.

Item {

    id: root

    property real stageTiltAngle: 0
    property int rotationRegime: MillingAngleCalculations.ROTATION_NEG_70
    property var chalkLocalAngles: []

    signal stageOrientationRequested(int rotationRegimeTarget,
                                     real tiltValue)

    property real centerX: width / 2
    property real centerY: height / 2

    // Per-beam chip lists: achievable { stageTiltDeg, relation,
    // rotationRegime } entries for BOTH regimes, in a FIXED canonical
    // order: -70 regime entries first, then 110, each sorted by tilt.
    // The order is deliberately independent of the CURRENT regime so
    // chip numbers are stable identities for positions - flipping the
    // regime swaps which group is dimmed, never the numbering.
    // Recomputed only when the chalk lines change.
    readonly property var _cycleLists: {
        var lists = {}
        var beams = ["SEM", "FIB", "GIS"]
        var regimes = [MillingAngleCalculations.ROTATION_NEG_70,
                       MillingAngleCalculations.ROTATION_110]
        for (var i = 0; i < beams.length; i++) {
            var combined = []
            for (var r = 0; r < regimes.length; r++) {
                var entries = MillingAngleCalculations.achievableTiltsForBeam(
                    chalkLocalAngles, regimes[r], beams[i],
                    AppConfig.stageTiltAngleMin, AppConfig.stageTiltAngleMax)
                for (var e = 0; e < entries.length; e++) {
                    combined.push({ stageTiltDeg: entries[e].stageTiltDeg,
                                    relation: entries[e].relation,
                                    rotationRegime: regimes[r] })
                }
            }
            lists[beams[i]] = combined
        }
        return lists
    }

    // Per-beam cycle indices (view state), reset when the lists change.
    property var _cycleIndices: ({ SEM: 0, FIB: 0, GIS: 0 })
    on_CycleListsChanged: _cycleIndices = { SEM: 0, FIB: 0, GIS: 0 }

    // Apply a specific achievable position for the beam (used by the
    // numbered chips; may switch the rotation regime). Exposed for
    // offscreen tests.
    function applyBeamEntry(beamName, entryIndex) {
        var list = _cycleLists[beamName]
        if (!list || entryIndex < 0 || entryIndex >= list.length)
            return
        var entry = list[entryIndex]
        root.stageOrientationRequested(entry.rotationRegime,
                                       entry.stageTiltDeg)
    }

    // Apply the next achievable position for the beam in the CURRENT
    // regime, wrapping around. Exposed for the page wiring and for
    // offscreen tests.
    function cycleBeam(beamName) {
        var list = (_cycleLists[beamName] || []).filter(function(e) {
            return e.rotationRegime === root.rotationRegime
        })
        if (list.length === 0)
            return
        var index = _cycleIndices[beamName] % list.length
        var updated = JSON.parse(JSON.stringify(_cycleIndices))
        updated[beamName] = (index + 1) % list.length
        _cycleIndices = updated
        root.stageOrientationRequested(root.rotationRegime,
                                       list[index].stageTiltDeg)
    }

    Canvas {

        id: staticCanvas
        anchors.fill: parent

        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)

            var beamLen = AppConfig.millingFigureBeamLineLength
            var lineOuter = AppConfig.sampleFigureBeamLabelRadius
                            - AppConfig.millingFigureBeamLabelOffset
            var gisLen = beamLen * AppConfig.millingFigureGisLineFraction
            var lineW = AppConfig.millingFigureLineWidth
            var beamColor = AppConfig.millingFigureBeamLineColor

            DiagramFunctions.drawRadialLine(
                ctx, root.centerX, root.centerY, lineOuter - beamLen,
                beamLen, MillingAngleCalculations.semScreenAngleDeg(),
                beamColor, lineW)
            DiagramFunctions.drawRadialLine(
                ctx, root.centerX, root.centerY, lineOuter - beamLen,
                beamLen, MillingAngleCalculations.fibScreenAngleDeg(),
                beamColor, lineW)
            DiagramFunctions.drawRadialLine(
                ctx, root.centerX, root.centerY, lineOuter - gisLen,
                gisLen, MillingAngleCalculations.gisScreenAngleDeg(),
                beamColor, lineW)
        }

    }

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

            readonly property var _pos:
                DiagramFunctions.getRadialLabelPosition(
                    root.centerX, root.centerY,
                    AppConfig.sampleFigureBeamLabelRadius,
                    0, modelData.angle, 0)

            // Cycling via the label moves within the current regime
            // only; the chips give access to both regimes.
            readonly property bool _canCycle:
                (root._cycleLists[modelData.name] || []).some(function(e) {
                    return e.rotationRegime === root.rotationRegime
                })

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
                hovered: beamLabelMA.containsMouse && beamLabel._canCycle
            }

            // Numbered position chips: one per achievable entry,
            // colored by its relation; clicking applies it directly.
            Grid {
                anchors.left: parent.right
                anchors.leftMargin: 5
                anchors.verticalCenter: parent.verticalCenter
                columns: AppConfig.sampleFigureChipMaxPerRow
                spacing: 3

                Repeater {

                    model: root._cycleLists[beamLabel.modelData.name]

                    delegate: Rectangle {

                        id: chip

                        required property var modelData
                        required property int index

                        // The chip lights up in its relation's highlight
                        // color when the stage is currently AT this
                        // position (same regime, tilt within the
                        // relation tolerance).
                        readonly property bool _active:
                            modelData.rotationRegime === root.rotationRegime
                            && Math.abs(root.stageTiltAngle
                                        - modelData.stageTiltDeg)
                               <= MillingAngleCalculations
                                      .RELATION_TOLERANCE_DEG

                        readonly property bool _otherRegime:
                            modelData.rotationRegime !== root.rotationRegime

                        readonly property color _relationColor:
                            modelData.relation === MillingAngleCalculations
                                .RELATION_PERPENDICULAR
                            ? AppConfig.perpendicularHighlightColor
                            : AppConfig.parallelHighlightColor

                        // The number stays neutral while the chip is
                        // available and takes the relation color when the
                        // stage is AT this position. The border carries
                        // the relation hue separately (see below), so the
                        // relation is readable at rest without activating.
                        readonly property color _numberColor:
                            _active ? _relationColor
                                    : AppConfig.millingFigureBeamLineColor

                        readonly property string _relationName:
                            modelData.relation === MillingAngleCalculations
                                .RELATION_PERPENDICULAR
                            ? "Perpendicular" : "Parallel"

                        // Milling angle at this entry's stage orientation
                        // (identity: stage tilt + regime offset).
                        readonly property real _millingAngleDeg:
                            MillingAngleCalculations.calculateMillingAngle(
                                modelData.stageTiltDeg,
                                modelData.rotationRegime)

                        // Full detail on hover. The rotation regime
                        // constants ARE the stage rotation angles
                        // (-70 / 110), so the regime value doubles as
                        // display text. Text is assembled here (not in
                        // Strings.qml) because every line is derived
                        // from the entry's data.
                        ToolTip.text: chip._relationName + " to "
                                      + beamLabel.modelData.name + "\n"
                                      + "Milling angle: "
                                      + chip._millingAngleDeg.toFixed(1)
                                      + "°\n"
                                      + "Stage tilt: "
                                      + chip.modelData.stageTiltDeg
                                            .toFixed(1) + "°\n"
                                      + "Stage rotation: "
                                      + chip.modelData.rotationRegime + "°"
                        ToolTip.visible: chipMA.containsMouse
                        ToolTip.delay: AppConfig.toolTipDelayMs
                        ToolTip.timeout: AppConfig.toolTipTimeoutMs

                        width: chipLabel.implicitWidth + 8
                        height: chipLabel.implicitHeight + 2
                        radius: 3
                        color: chip._active ? Qt.alpha(chip._relationColor,
                                                       0.18)
                                            : "transparent"
                        border.width: 1
                        // Border always carries the relation hue (mint =
                        // perpendicular, pink = parallel) so the chip's
                        // relation reads at a glance. Dim at rest, full
                        // when the stage is AT this position; the
                        // translucent fill above is the other "you are
                        // here" cue. Hover overrides to the accent color.
                        border.color: chipMA.containsMouse
                                      ? AppConfig.universalAccent
                                      : Qt.alpha(chip._relationColor,
                                                 chip._active ? 1.0 : 0.45)
                        // Other-regime chips are dimmed: clicking them
                        // also flips the stage rotation.
                        opacity: chip._otherRegime ? 0.55 : 1.0

                        Label {
                            id: chipLabel
                            anchors.centerIn: parent
                            text: chip.index + 1
                            font.pixelSize:
                                AppConfig.millingFigureLabelFontSize - 4
                            color: chip._numberColor
                        }

                        MouseArea {
                            id: chipMA
                            anchors.fill: parent
                            anchors.margins: -3
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.applyBeamEntry(
                                           beamLabel.modelData.name,
                                           chip.index)
                        }

                    }

                }

            }

            MouseArea {
                id: beamLabelMA
                anchors.fill: parent
                anchors.margins: -6
                hoverEnabled: true
                cursorShape: beamLabel._canCycle ? Qt.PointingHandCursor
                                                 : Qt.ArrowCursor
                onClicked: root.cycleBeam(beamLabel.modelData.name)
            }

        }

    }

}
