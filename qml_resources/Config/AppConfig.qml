pragma Singleton
import QtQuick
import QtQuick.Controls.Universal

QtObject {

    // Theme colors
    readonly property int universalTheme: Universal.Dark
    readonly property color universalAccent: "#2ea2ec"
    readonly property color universalForeground: "#ffffff"
    readonly property color universalBackground: "#1e2c36" //"#273945"
    readonly property color textDisabledColor: "#8498a4"
    readonly property color placeholderTextColor: "#9CBBD2" //eb70a9 (catbug oven mitt)

    // Main window
    readonly property int mainWindowWidth: 910
    readonly property int mainWindowHeight: 920
    readonly property int mainWindowMinimumWidth: 700
    readonly property int mainWindowMinimumHeight: 700

    // Compact mode is responsive: the UI switches to the compact layout
    // (subtitles hidden, header spacers collapsed) when the window is dragged to
    // or below compactBreakpointWidth/Height in either dimension.
    // compactWindow* is the snap size applied by the Settings-page "Compact
    // Mode" checkbox (main.qml applyCompact). Keep both dimensions at or below
    // the breakpoints below — and mainWindow* above them — or a checkbox
    // toggle would land on the wrong side of the cutoff and the checkbox
    // would immediately fall out of sync with the layout.
    // compactWindowMinimum* is the ApplicationWindow's permanent floor (main.qml),
    // so the window can be shrunk into that range.
    readonly property int compactWindowWidth: 740
    readonly property int compactWindowHeight: 560
    readonly property int compactWindowMinimumWidth: 560
    readonly property int compactWindowMinimumHeight: 520

    // Responsive cutoff — tune these to move where the compact layout engages.
    readonly property int compactBreakpointWidth: 760
    readonly property int compactBreakpointHeight: 680

    // Tooltip durations
    readonly property int toolTipDelayMs: 1500
    readonly property int toolTipTimeoutMs: 10000

    // Buttons
    readonly property int buttonRadius: 4
    readonly property int buttonPadding: 16
    readonly property int stageScanBigButtonPadding: 10
    readonly property int stagePositionsButtonWidth: 140
    readonly property int stageScanBigButtonWidth: 245
    readonly property int stageScanBigButtonHeight: 151

    // Dialogs
    readonly property int dialogDefaultWidth: 360
    // Wider variant for dialogs with longer-form options — e.g. the Cryo
    // template save options, whose scope labels read better on one line.
    readonly property int dialogWideWidth: 460
    readonly property int dialogPadding: 16

    // Side Bar
    readonly property color sideBarBackground: "#263640" //"#34454f"
    readonly property int navItemFontSize: 18
    readonly property int sideBarWidth: 200
    readonly property int sideBarMargins: 8
    readonly property int sideBarColumnSpacing: 4

    // Status Bar
    readonly property int statusBarLabelFontSize: 16
    readonly property int statusBarHorizontalMargin: 12
    readonly property int statusBarProgressWidth: 300
    readonly property int statusBarIndicatorLabelWidth: 200

    // Activity container — colors
    readonly property color activityContainerBackground: "#263640"
    readonly property color activityContainerIdleBorder: "#2e3e49"
    readonly property color activityCompleteColor: "#33ff33"
    readonly property color activityRunningColor: "#2ea2ec"
    readonly property color activityExceptionColor: "#ffc633"
    readonly property color activitySeparatorColor: "#fcff93"
    readonly property color activityRemoveButtonColor: "#a8afb3"
    readonly property color activityParameterSummaryColor: "#9CBBD2" //"#eb70a9"
    readonly property color activityDragHandleColor: "#9CBBD2"

    // Activity container — layout
    readonly property int activityContainerRadius: 6
    readonly property int activityContainerBorderWidth: 2
    readonly property int activityContainerPadding: 12
    readonly property int activityContainerSideMargin: 12
    readonly property int activityContainerSpacing: 18
    readonly property int activityContainerHeaderHeight: 30
    readonly property int activityIconTitleGap: 8
    readonly property int activityContainerMaxWidthRoomTemp: 400
    readonly property int activityContainerMaxWidthCryoTemp: 500
    readonly property int activityGap: 8
    readonly property int activityFormRowSpacing: 8
    readonly property int activityFormColumnSpacing: 16
    readonly property int activityStagePositionsRowSpacing: 4
    readonly property int activityStagePositionsColumnSpacing: 8
    readonly property int activityStagePositionsListMargin: 10
    readonly property int activityStagePositionValueColumnWidth: 140
    readonly property int activityStackHeaderColumnSpacing: 2

    // Activity container — icons & controls
    readonly property int activityStatusIconSize: 20
    readonly property int activityChevronIconSize: 18
    readonly property int activityDragHandleWidth: 4
    readonly property int activityDragHandleHeight: 20

    // Activity container — typography
    readonly property int activityTitleFontSize: 16
    readonly property int activityParameterSummaryFontSize: 14

    // Activity container — icon paths
    readonly property url iconChevronRight: "../assets/chevron-right-square.svg"
    readonly property url iconCheckmark: "../assets/checkmark.svg"
    readonly property url iconWarning: "../assets/warning.svg"
    readonly property url iconClose: "../assets/cross.svg"

    // Activity container — animations
    readonly property int activityExpandDurationMs: 125
    readonly property int activityChevronRotateDurationMs: 125
    readonly property int activityBorderColorDurationMs: 150

    // Pages
    readonly property int pageMargin: 20
    readonly property int pageHeadingFontSize: 20
    readonly property int pageBodyFontSize: 16
    readonly property int pageHeadingSpacerHeight: 20
    readonly property int pageSectionSpacing: 12 // Used for column layouts, for example
    readonly property int buttonRowSpacing: 12  // Used for the bottom button row layout
    readonly property int stageZSeparatorHeight: 40
    readonly property int settingsFormRowSpacing: 12
    readonly property int settingsFormColumnSpacing: 30
    readonly property int settingsPageMaxWidth: 400

    // Generalized activity parameters
    readonly property int chamberRecoverySpinBoxMin: 0
    readonly property int chamberRecoverySpinBoxMax: 999
    readonly property int chamberRecoverySpinBoxDefaultSputterDuration: 0
    readonly property int chamberRecoverySpinBoxDefaultGISDepositionDuration: 30
    readonly property int chamberRecoverySpinBoxDefaultGISPurgeDuration: 30

    // Sputter coat activity parameters
    readonly property int sputterCoatDefaultDuration: 120
    readonly property int sputterCoatDurationMin: 1
    readonly property int sputterCoatDurationMax: 999
    readonly property int bulkSputterCoatDefaultDuration: 120
    readonly property int lamellaSputterCoatDefaultDuration: 12
    // Sputter coat HFW (microns) — bounds for the Settings-page spin box.
    // Mirror defaults.py SPUTTER_COAT_HFW_UM_MIN / _MAX (the Python
    // SettingsController is the load-bearing clamp on write).
    readonly property int sputterCoatHfwMin: 1
    readonly property int sputterCoatHfwMax: 10000

    // GIS deposition activity parameters
    readonly property int gisDepositionDefaultDuration: 90
    readonly property int gisDepositionDurationMin: 1
    readonly property int gisDepositionDurationMax: 999

    // GIS purge activity parameters
    readonly property int gisPurgeDefaultDuration: 120
    readonly property int gisPurgeDurationMin: 1
    readonly property int gisPurgeDurationMax: 999

    // Stage / Scan page
    // Slider tick range. The physical Z motion per tick is determined by
    // STAGE_Z_STEP_SIZE_M in defaults.py, not by these values.
    readonly property int stageZSliderMin: -25
    readonly property int stageZSliderMax: 25

    // Lift-out Calculator parameters
    readonly property int liftoutCalculatorContainerWidth: 340
    readonly property int shuttlePretiltAngleDefault: 35
    readonly property int shuttlePretiltAngleMin: 0
    readonly property int shuttlePretiltAngleMax: 90
    readonly property int liftoutTiltAngleMin: -15
    readonly property int liftoutTiltAngleMax: 90
    readonly property int attachTiltAngleMin: -15
    readonly property int attachTiltAngleMax: 90
    readonly property int liftoutTiltDefaultTopdownMinus70: 20
    readonly property int liftoutTiltDefaultPlanarMinus70: 15
    readonly property int liftoutTiltDefaultPlanar110: 15
    readonly property int attachTiltDefaultTopdownMinus70: 32
    readonly property int attachTiltDefaultPlanarMinus70: 25
    readonly property int attachTiltDefaultPlanar110: 5

    // Milling Angle Page parameters
    //
    // Stage tilt limits are the physical stage range. Milling angle
    // limits and defaults are NOT stored here: they are derived from the
    // stage tilt limits and the geometry in Js/millingAngleCalculations.js
    // (see MillingAngleCalculator.qml), so they can never drift out of
    // sync with the milling-angle formulas.
    readonly property real stageTiltAngleDefault: 0.0
    readonly property real stageTiltAngleMin: -10.0
    readonly property real stageTiltAngleMax: 60.0

    // Milling Angle Page figure parameters
    //
    // millingFigureScale enlarges the whole shuttle figure: bump this one
    // number to grow the gauge,
    // the shuttle SVG, and the beam labels together. It multiplies the
    // spatial geometry only (radii and the beam line length); fonts, tick
    // length, label offsets, and line widths stay at fixed pixel sizes so
    // text and strokes keep their weight as the figure grows. The shuttle
    // image height and bottom clearance derive from the arc radius below,
    // so they scale automatically. Fold any fixed token into the multiply
    // if you want it to scale too.
    readonly property real millingFigureScale: 1.1
    readonly property real millingFigureArcRadius: 210 * millingFigureScale
    // Beam label model: the SEM/FIB/GIS labels all sit on one common
    // arc at millingFigureBeamLabelRadius from the fiducial. Each beam
    // line grows inward from its label: the line's outer end is at
    // (labelRadius - labelOffset) and it extends toward the center by
    // its length. Shortening a line (e.g. GIS) therefore shortens its
    // inward reach without moving its label off the shared arc.
    readonly property real millingFigureBeamLabelRadius: 165 * millingFigureScale
    readonly property real millingFigureBeamLabelOffset: 10
    readonly property real millingFigureBeamLineLength: 80 * millingFigureScale
    // GIS line length as a fraction of the SEM/FIB beam line length.
    readonly property real millingFigureGisLineFraction: 0.55
    // Sample figure parameters (chalk line diagram). The sample sits on
    // the grid; both rectangles rotate with the pre-tilted shuttle face
    // (regime-dependent). Beam labels reuse the milling-figure beam
    // styling (label offset, line length, GIS fraction) at this figure's
    // own label radius.
    // sampleFigureScale enlarges the whole sample (chalk-line) figure the
    // same way millingFigureScale does for the shuttle: one number grows
    // the beam-label ring and the sample/grid rectangles together. The
    // chip allowance and chalk line width are left fixed on purpose (the
    // chips are font-sized and the chalk weight should stay constant).
    readonly property real sampleFigureScale: 1.1
    readonly property real sampleFigureBeamLabelRadius: 185 * sampleFigureScale
    readonly property real sampleFigureSampleRectWidth: 120 * sampleFigureScale
    readonly property real sampleFigureSampleRectHeight: 28 * sampleFigureScale
    readonly property real sampleFigureGridRectWidth: 145 * sampleFigureScale
    readonly property real sampleFigureGridRectHeight: 3 * sampleFigureScale
    readonly property real sampleFigureChalkLineWidth: 3
    // Right-side width reserve for the numbered position chips next to
    // the beam labels (static so the figure doesn't resize as chalk
    // lines are added). Sized to fit sampleFigureChipMaxPerRow chips
    // across; widen this together with that token if you raise it.
    readonly property real sampleFigureChipAllowance: 56
    readonly property int sampleFigureChipMaxPerRow: 3
    readonly property color sampleFigureChalkLineColor: "#E6EB70"
    readonly property color sampleFigureSampleFillColor: "#3a4a55"
    readonly property color perpendicularHighlightColor: "#70EBB2"
    readonly property color parallelHighlightColor: "#eb70a9"
    readonly property real millingFigureTickLength: 12
    readonly property real millingFigureLabelOffset: 6
    readonly property real millingFigureLineWidth: 1.0
    readonly property real millingFigureIndicatorWidth: 3
    readonly property color millingFigureIndicatorColor: "#2ea2ec"
    // Half-duration of the regime flip animation (squash to zero, swap
    // the mirrored SVG, expand back). Total flip = 2x this value.
    readonly property int millingFigureFlipHalfDurationMs: 180
    // Show the stage-plane guide (the needle-like faint radius from the
    // gauge center to the active arm's tick position).
    readonly property bool millingFigureStagePlaneGuideVisible: false
    readonly property int millingFigureLabelFontSize: 16
    // Shuttle SVG fiducial (grid / rotation point) position as fractions
    // of the image size, measured from the fiducial circles embedded in
    // the SVG assets ("path14-4-3" in the -70 file, "path14-49" in the
    // 110 file). Re-measure if the assets are regenerated.
    readonly property real millingFigureFiducialFracXNeg70: 0.70261
    readonly property real millingFigureFiducialFracX110: 0.29742
    readonly property real millingFigureFiducialFracY: 0.09111
    // Shuttle image height, derived from the gauge arc radius so the
    // rotating shuttle can never overlap the gauge arc. The divisor is
    // the maximum distance from the gauge center to the swept image
    // silhouette within the arc's angular sectors, over the full stage
    // tilt range and both rotation regimes, with 3% margin.
    readonly property real millingFigureShuttleImageHeight:
        millingFigureArcRadius / 2.2013 * 0.97
    readonly property real millingFigureBottomClearance:
        millingFigureShuttleImageHeight * 1.641 + 5
    readonly property color millingFigureLineColor: "#ffffff"
    readonly property color millingFigureBeamLineColor: "#ffffff"

}
