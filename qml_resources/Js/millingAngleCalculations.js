.pragma library

// =============================================================================
// MILLING ANGLE CALCULATIONS
//
// Pure geometry library for the Milling Angle Calc page. This file is the
// single source of truth for the Hydra NSR milling-angle geometry:
//
//   * The SEM column is vertical (90 degrees from horizontal).
//   * The FIB column is 52 degrees from the SEM, on the RIGHT side of the
//     SEM in the side-view figures (38 degrees above horizontal).
//   * The AutoGrid shuttle holds the grid/sample on a 35 degree pre-tilt
//     face.
//   * The stage rotation position determines the sign with which the
//     pre-tilt enters the milling-angle relationship:
//       - At stage rotation  -70 deg the pre-tilt SUBTRACTS:
//             millingAngle = stageTilt + (38 - 35) = stageTilt + 3
//       - At stage rotation +110 deg the pre-tilt ADDS:
//             millingAngle = stageTilt + (38 + 35) = stageTilt + 73
//
//   Anchor points (validated in tests/test_millingAngleCalculations.mjs):
//       rot -70, tilt  0  ->  milling  3
//       rot 110, tilt  0  ->  milling 73
//       rot 110, tilt 17  ->  milling 90  (FIB perpendicular to sample)
//
// Angle conventions:
//   * All angles are in degrees.
//   * "Screen angles" use the diagram convention from diagramFunctions.js:
//     0 deg = horizontal right, positive = counter-clockwise.
//   * QML Item.rotation is clockwise-positive; use shuttleItemRotation()
//     for values fed directly to Image/Item rotation properties.
//
// Stage tilt limits (AppConfig.stageTiltAngleMin / Max) are NOT duplicated
// here. Functions that need them take them as arguments so AppConfig
// remains the single source of truth for UI limits.
// =============================================================================

// -----------------------------------------------------------------------------
// GEOMETRY CONSTANTS
// -----------------------------------------------------------------------------

// SEM column, measured from horizontal (vertical column).
var SEM_ANGLE_FROM_HORIZONTAL_DEG = 90.0

// FIB column, measured from the SEM column.
var FIB_ANGLE_FROM_SEM_DEG = 52.0

// FIB column, measured from horizontal. (90 - 52 = 38)
var FIB_ANGLE_FROM_HORIZONTAL_DEG =
        SEM_ANGLE_FROM_HORIZONTAL_DEG - FIB_ANGLE_FROM_SEM_DEG

// AutoGrid shuttle pre-tilt of the grid/sample face.
var SHUTTLE_PRE_TILT_DEG = 35.0

// GIS needle, measured from the SEM column, on the FIB side of the SEM. Port 4 (Multichem).
var GIS_ANGLE_FROM_SEM_DEG = 40.0

// -----------------------------------------------------------------------------
// STAGE ROTATION REGIMES
// -----------------------------------------------------------------------------

// The two supported stage rotation positions. Values are the actual stage
// rotation angles in degrees so logs and tooltips can display them directly.
var ROTATION_NEG_70 = -70
var ROTATION_110 = 110

// Sign with which the shuttle pre-tilt enters the milling-angle formula
// for a given rotation regime. Throws on unknown regimes: every call site
// must state its regime explicitly, and silent fallbacks would hide bugs.
function preTiltSign(rotationRegime) {
    if (rotationRegime === ROTATION_NEG_70)
        return -1.0
    if (rotationRegime === ROTATION_110)
        return 1.0
    throw new Error("Unknown stage rotation regime: " + rotationRegime)
}

// Constant offset between stage tilt and milling angle for a regime.
//   rot -70 ->  3 degrees
//   rot 110 -> 73 degrees
function millingAngleOffset(rotationRegime) {
    return FIB_ANGLE_FROM_HORIZONTAL_DEG
            + preTiltSign(rotationRegime) * SHUTTLE_PRE_TILT_DEG
}

// -----------------------------------------------------------------------------
// BEAM PRESETS
// -----------------------------------------------------------------------------

// Stage orientation presets applied when the user clicks a beam label in
// the diagrams:
//   SEM - rot -70, tilt 35: the shuttle face is horizontal (face angle
//         35 - 35 = 0), i.e. the sample squarely faces the SEM.
//   FIB - rot 110, tilt 17: the shuttle face is perpendicular to the
//         FIB (milling angle 90).
//   GIS - rot -70, tilt 60: deposition orientation (max tilt at -70).
function beamPreset(beamName) {
    if (beamName === "SEM")
        return { rotationRegime: ROTATION_NEG_70, stageTiltDeg: 35.0 }
    if (beamName === "FIB")
        return { rotationRegime: ROTATION_110, stageTiltDeg: 17.0 }
    if (beamName === "GIS")
        return { rotationRegime: ROTATION_NEG_70, stageTiltDeg: 60.0 }
    throw new Error("Unknown beam preset: " + beamName)
}

// -----------------------------------------------------------------------------
// MILLING ANGLE <-> STAGE TILT CONVERSIONS
// -----------------------------------------------------------------------------

// Milling angle (degrees) for a given stage tilt angle (degrees) in the
// given rotation regime.
function calculateMillingAngle(stageTiltDeg, rotationRegime) {
    return stageTiltDeg + millingAngleOffset(rotationRegime)
}

// Stage tilt angle (degrees) for a given milling angle (degrees) in the
// given rotation regime. Exact inverse of calculateMillingAngle().
function calculateStageTilt(millingAngleDeg, rotationRegime) {
    return millingAngleDeg - millingAngleOffset(rotationRegime)
}

// Reachable milling-angle range for a regime, given the stage tilt limits
// (pass AppConfig.stageTiltAngleMin / stageTiltAngleMax from QML).
// Returns { min, max }.
function millingAngleRange(rotationRegime, stageTiltMinDeg, stageTiltMaxDeg) {
    return {
        min: calculateMillingAngle(stageTiltMinDeg, rotationRegime),
        max: calculateMillingAngle(stageTiltMaxDeg, rotationRegime)
    }
}

// -----------------------------------------------------------------------------
// SCREEN-ANGLE HELPERS (for the diagram canvases and rotating images)
// -----------------------------------------------------------------------------

// SEM line screen angle: straight up.
function semScreenAngleDeg() {
    return 90.0
}

// FIB line screen angle: 38 degrees above horizontal on the RIGHT side
// of the SEM (counter-clockwise from horizontal right).
function fibScreenAngleDeg() {
    return FIB_ANGLE_FROM_HORIZONTAL_DEG
}

// GIS line screen angle: 73.5 degrees from the SEM toward the FIB side
// (90 - 73.5 = 16.5 degrees above horizontal right).
function gisScreenAngleDeg() {
    return SEM_ANGLE_FROM_HORIZONTAL_DEG - GIS_ANGLE_FROM_SEM_DEG
}

// Rotation to apply to the shuttle/sample graphics for a given stage tilt,
// in QML Item.rotation convention (clockwise-positive). The SVGs are drawn
// at stage tilt 0; positive stage tilt rotates the shuttle CLOCKWISE on
// screen. This sign is forced by the anchor points: at rot 110 the sample
// face must reach -52 deg screen angle (perpendicular to the 38 deg FIB
// line) at stage tilt +17. Confirmed against the AutoScript manual:
// MultiChemInsertPosition.ION_DEFAULT corresponds to tilt angle 52 deg,
// i.e. positive +52 tilt brings a flat stage surface perpendicular to
// the ion column.
function shuttleItemRotation(stageTiltDeg) {
    return stageTiltDeg
}

// Screen angles of the two arms of the stage plane at a given stage tilt
// (counter-clockwise from horizontal right). The tilt gauge in the figures
// is a protractor of the stage plane itself: tick marks for negative tilts
// sit on the RIGHT arm's path (rising toward the FIB for negative tilt),
// tick marks for positive tilts sit on the LEFT arm's path. Both arms
// rotate with the shuttle (clockwise for positive tilt), so at tilt t:
//   right arm: 0 - t    (tilt -10 -> screen +10)
//   left arm:  180 - t  (tilt +60 -> screen +120)
function stagePlaneRightArmScreenAngleDeg(stageTiltDeg) {
    return -stageTiltDeg
}

function stagePlaneLeftArmScreenAngleDeg(stageTiltDeg) {
    return 180.0 - stageTiltDeg
}

// Screen angle for a tilt gauge tick mark: negative tilt values are marked
// on the right arm's path, positive (and zero) on the right/left arm
// respectively per the figure design ("0" is labeled at the right
// horizontal; positive ticks ascend the left arc).
function tiltTickScreenAngleDeg(tiltValueDeg) {
    if (tiltValueDeg > 0)
        return stagePlaneLeftArmScreenAngleDeg(tiltValueDeg)
    return stagePlaneRightArmScreenAngleDeg(tiltValueDeg)
}

// Screen angle of the grid/sample face plane for a given stage tilt and
// regime (counter-clockwise from horizontal right). Useful for chalk lines
// and the sample-figure plane. At tilt 0:
//   rot -70 -> +35 (face tips up toward the FIB on the right)
//   rot 110 -> -35 (face tips down away from the FIB)
//
// Identity (verified in tests):
//   calculateMillingAngle(t, r) ===
//       fibScreenAngleDeg() - sampleFaceScreenAngleDeg(t, r)
function sampleFaceScreenAngleDeg(stageTiltDeg, rotationRegime) {
    return -preTiltSign(rotationRegime) * SHUTTLE_PRE_TILT_DEG - stageTiltDeg
}

// -----------------------------------------------------------------------------
// CHALK LINE RELATIONS (sample diagram)
// -----------------------------------------------------------------------------
//
// Chalk lines represent FIB cuts on the sample. They live in the sample
// plane's LOCAL frame and are stored by their local angle. The sample
// plane item rotates by -sampleFaceScreenAngleDeg (QML clockwise
// convention), so a line at local angle L appears on screen at the
// global angle:
//
//     G = L + sampleFaceScreenAngleDeg(stageTilt, regime)
//
// A chalk line is created along the FIB direction, so its local angle is
// fibScreenAngleDeg() - sampleFaceScreenAngleDeg(...) - which by the
// milling angle identity IS the milling angle at creation time. Every
// chalk line therefore records the milling angle it was cut at.
//
// Relations (parallel / perpendicular to a beam) are properties of
// undirected lines, so all comparisons are made modulo 180 degrees.

var RELATION_TOLERANCE_DEG = 0.1

var RELATION_PARALLEL = "parallel"
var RELATION_PERPENDICULAR = "perpendicular"
var RELATION_NONE = "none"

// Local (sample-frame) angle of a chalk line created along the FIB at
// the given stage orientation. Equal to the milling angle (identity:
// milling = fibScreenAngle - faceScreenAngle).
function chalkLineLocalAngleDeg(stageTiltDeg, rotationRegime) {
    return fibScreenAngleDeg()
           - sampleFaceScreenAngleDeg(stageTiltDeg, rotationRegime)
}

// Screen-frame (global) angle of a chalk line at the given stage
// orientation.
function chalkLineGlobalAngleDeg(localAngleDeg, stageTiltDeg,
                                 rotationRegime) {
    return localAngleDeg
           + sampleFaceScreenAngleDeg(stageTiltDeg, rotationRegime)
}

// Screen angle of a beam by name ("SEM" / "FIB" / "GIS").
function beamScreenAngleDeg(beamName) {
    if (beamName === "SEM")
        return semScreenAngleDeg()
    if (beamName === "FIB")
        return fibScreenAngleDeg()
    if (beamName === "GIS")
        return gisScreenAngleDeg()
    throw new Error("Unknown beam: " + beamName)
}

// Minimal angular difference between two undirected lines, in [0, 90].
function lineAngleDifferenceDeg(angleADeg, angleBDeg) {
    var d = (angleADeg - angleBDeg) % 180
    if (d < 0)
        d += 180
    return Math.min(d, 180 - d)
}

// Relation of a chalk line (by local angle) to a beam at the given
// stage orientation: RELATION_PARALLEL, RELATION_PERPENDICULAR, or
// RELATION_NONE.
function chalkLineRelation(localAngleDeg, stageTiltDeg, rotationRegime,
                           beamName) {
    var g = chalkLineGlobalAngleDeg(localAngleDeg, stageTiltDeg,
                                    rotationRegime)
    var d = lineAngleDifferenceDeg(g, beamScreenAngleDeg(beamName))
    if (d <= RELATION_TOLERANCE_DEG)
        return RELATION_PARALLEL
    if (Math.abs(d - 90) <= RELATION_TOLERANCE_DEG)
        return RELATION_PERPENDICULAR
    return RELATION_NONE
}

// All stage tilts within [tiltMinDeg, tiltMaxDeg] at which the chalk
// line (by local angle) attains the given relation to the beam, in the
// given rotation regime. Solving
//     localAngle + face(t) = beamAngle + offset + k * 180
// with face(t) = -preTiltSign * 35 - t gives
//     t = localAngle - preTiltSign * 35 - beamAngle - offset - k * 180.
// The stage tilt range spans less than 180 degrees, so at most one k
// yields an in-range solution per relation.
function achievableTiltsForRelation(localAngleDeg, rotationRegime,
                                    beamName, relation,
                                    tiltMinDeg, tiltMaxDeg) {
    var offset
    if (relation === RELATION_PARALLEL)
        offset = 0
    else if (relation === RELATION_PERPENDICULAR)
        offset = 90
    else
        throw new Error("Unknown relation: " + relation)

    var base = localAngleDeg
               - preTiltSign(rotationRegime) * SHUTTLE_PRE_TILT_DEG
               - beamScreenAngleDeg(beamName)
               - offset
    var tilts = []
    var kMin = Math.ceil((base - tiltMaxDeg) / 180)
    var kMax = Math.floor((base - tiltMinDeg) / 180)
    for (var k = kMin; k <= kMax; k++) {
        tilts.push(base - k * 180)
    }
    return tilts
}

// Combined, sorted cycle list for one beam over a set of chalk lines
// (array of local angles) in the given regime: every achievable
// { stageTiltDeg, relation } within the tilt limits, deduplicated
// (0.01 degree tolerance) and sorted ascending by tilt. This is the
// list a beam-label click cycles through. Cross-regime cycling (a
// possible future extension) is this same function called once per
// regime.
function achievableTiltsForBeam(localAnglesDeg, rotationRegime,
                                beamName, tiltMinDeg, tiltMaxDeg) {
    var entries = []
    var relations = [RELATION_PERPENDICULAR, RELATION_PARALLEL]
    for (var i = 0; i < localAnglesDeg.length; i++) {
        for (var r = 0; r < relations.length; r++) {
            var tilts = achievableTiltsForRelation(
                localAnglesDeg[i], rotationRegime, beamName,
                relations[r], tiltMinDeg, tiltMaxDeg)
            for (var j = 0; j < tilts.length; j++) {
                var duplicate = false
                for (var e = 0; e < entries.length; e++) {
                    if (Math.abs(entries[e].stageTiltDeg - tilts[j]) < 0.01) {
                        duplicate = true
                        break
                    }
                }
                if (!duplicate) {
                    entries.push({ stageTiltDeg: tilts[j],
                                   relation: relations[r] })
                }
            }
        }
    }
    entries.sort(function(a, b) { return a.stageTiltDeg - b.stageTiltDeg })
    return entries
}
