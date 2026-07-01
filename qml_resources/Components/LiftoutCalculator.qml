import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../Config"
import "../Components"

// Lift-out Calculator
//
// Assists the user with calculating the angles necessary for cryo
// lift-out procedures using a pre-tilted shuttle (such as the shuttles
// included with the Hydra NSR and Aquilos 2). Pure UI math: no hardware
// calls and no controller. The calculator does hold session-scoped,
// in-QML input state (per-type memory, see below), but there is no
// cross-restart persistence — every launch starts from AppConfig
// defaults.
//
// Lift-out types
//   • Top-down lift-out, -70° rotation
//   • Planar lift-out,   -70° rotation
//   • Planar lift-out,   110° rotation
//
// Reset
//   Restores all three inputs to defaults for the currently-selected
//   type only. Other types' remembered values are left untouched.
//   Reset writes the defaults into both the store slot and the spin
//   boxes so it persists across later type switches.
//
// Output bindings
//   The three output Labels (FIB stage tilt, FIB milling at -70°,
//   SEM tilt at -70°) are pure QML bindings that derive from a single
//   source of truth: _fibStageTiltAngle on the root, which switches on
//   the combo's currentIndex. _fibMillingAngleMinus70 and
//   _semTiltAngleMinus70 derive in turn from _fibStageTiltAngle plus
//   the pre-tilt. The dependency graph propagates automatically — no
//   imperative recompute calls on input changes.
//
// Cross-component publishing
//   `selectedTypeIndex` is exposed read-only so the parent page can
//   drive a per-type description block off the same combo selection.
//
// Geometry constants
//   38° is the FIB-SEM coincidence-point angle of the column. The 90°
//   in the top-down formula is the geometry's normal-to-rotation-axis
//   term. Both are physical column-geometry constants, not user inputs.

GroupBox {

    id: root

    Layout.fillWidth: true
    focusPolicy: Qt.StrongFocus
    background: Rectangle {
        anchors.fill: parent
        color: "transparent"
    }

    // ------------------------------------------------------------------
    // Published state — the parent page reads this to drive the
    // per-type description block. Read-only: writes are not forwarded
    // back to the combo.
    // ------------------------------------------------------------------

    readonly property int selectedTypeIndex: liftoutCalcComboBox.currentIndex

    // ------------------------------------------------------------------
    // Uniform spin box width: widest measured member in THIS group. Each
    // CustomSpinBox still self-measures its own implicitWidth; we just bind
    // every box to the group max so they line up across liftout types.
    // Loop-safe because implicitWidth doesn't depend on preferredWidth/width.
    // ------------------------------------------------------------------

    readonly property real _spinBoxWidth: Math.max(
        shuttlePretiltAngleSB.implicitWidth,
        liftoutTiltAngleSB.implicitWidth,
        attachTiltAngleSB.implicitWidth)

    // ------------------------------------------------------------------
    // Geometry constant — FIB column's coincidence-point angle relative
    // to the SEM, in degrees. Appears in all three FIB-tilt formulas
    // and in the FIB milling angle derivation.
    // ------------------------------------------------------------------

    readonly property int _fibSemCoincidenceDeg: 38

    // ------------------------------------------------------------------
    // Single source of truth — FIB stage tilt angle (degrees).
    //   P = shuttle pre-tilt
    //   L = lift-out tilt
    //   A = attach tilt
    //   k = _fibSemCoincidenceDeg
    // Top-down  (-70):  (90 - (P + k) + L) - A
    // Planar    (-70):  (A - L) - (k - P)
    // Planar    (110):  (L - A) - (k - P)
    // ------------------------------------------------------------------

    readonly property int _fibStageTiltAngle: {
        const P = shuttlePretiltAngleSB.value
        const L = liftoutTiltAngleSB.value
        const A = attachTiltAngleSB.value
        const k = root._fibSemCoincidenceDeg
        switch (liftoutCalcComboBox.currentIndex) {
            case 0: return (90 - (P + k) + L) - A
            case 1: return (A - L) - (k - P)
            case 2: return (L - A) - (k - P)
        }
        return 0
    }

    // FIB milling angle when the shuttle is at -70° rotation.
    //   = k + FIB_stage_tilt - P
    readonly property int _fibMillingAngleMinus70:
        root._fibSemCoincidenceDeg
        + root._fibStageTiltAngle
        - shuttlePretiltAngleSB.value

    // SEM tilt angle when the shuttle is at -70° rotation.
    //   = P + FIB_milling_angle
    readonly property int _semTiltAngleMinus70:
        shuttlePretiltAngleSB.value
        + root._fibMillingAngleMinus70

    // ------------------------------------------------------------------
    // Per-type defaults. Used to seed the store at construction and by
    // Reset. Index convention matches liftoutCalcComboBox:
    //   0 = Top-down -70, 1 = Planar -70, 2 = Planar 110.
    // Shuttle Pre-tilt has one AppConfig default shared across types
    // (it is per-type for UI consistency, not because the physical
    // default differs — see header).
    // ------------------------------------------------------------------

    function _liftoutTiltDefaultFor(idx) {
        switch (idx) {
            case 0: return AppConfig.liftoutTiltDefaultTopdownMinus70
            case 1: return AppConfig.liftoutTiltDefaultPlanarMinus70
            case 2: return AppConfig.liftoutTiltDefaultPlanar110
        }
        return 0
    }

    function _attachTiltDefaultFor(idx) {
        switch (idx) {
            case 0: return AppConfig.attachTiltDefaultTopdownMinus70
            case 1: return AppConfig.attachTiltDefaultPlanarMinus70
            case 2: return AppConfig.attachTiltDefaultPlanar110
        }
        return 0
    }

    function _shuttlePretiltDefaultFor(idx) {
        // Same default for every type by design (see header).
        return AppConfig.shuttlePretiltAngleDefault
    }

    // ------------------------------------------------------------------
    // Per-type session store (save/restore buffer).
    //
    // Seeded once at construction from the per-type defaults above. The
    // store is the single source of truth for REMEMBERED values; the
    // spin boxes are the source of truth for the CURRENTLY DISPLAYED
    // type. They are reconciled only on a type switch and by Reset.
    // ------------------------------------------------------------------

    property var _liftoutTiltByType: [
        AppConfig.liftoutTiltDefaultTopdownMinus70,
        AppConfig.liftoutTiltDefaultPlanarMinus70,
        AppConfig.liftoutTiltDefaultPlanar110
    ]

    property var _attachTiltByType: [
        AppConfig.attachTiltDefaultTopdownMinus70,
        AppConfig.attachTiltDefaultPlanarMinus70,
        AppConfig.attachTiltDefaultPlanar110
    ]

    property var _shuttlePretiltByType: [
        AppConfig.shuttlePretiltAngleDefault,
        AppConfig.shuttlePretiltAngleDefault,
        AppConfig.shuttlePretiltAngleDefault
    ]

    // The lift-out type the spin boxes currently represent. Updated
    // ONLY by _loadStoreIntoSpinBoxes. Save-on-leave keys off THIS, not
    // off liftoutCalcComboBox.currentIndex, because the combo index can
    // advance before a deferred editor commit has flushed into `value`
    // (the bug this design fixes — see header).
    property int _activeTypeIndex: 0

    // Reassign whole arrays (slice + replace) rather than mutating in
    // place: an in-place a[i] = v does NOT emit the property-changed
    // signal in QML. Reassignment keeps the store observable and
    // future-proofs against a later reactive consumer.

    function _writeStore(idx, shuttle, liftout, attach) {
        var s = _shuttlePretiltByType.slice(); s[idx] = shuttle
        var l = _liftoutTiltByType.slice();    l[idx] = liftout
        var a = _attachTiltByType.slice();     a[idx] = attach
        _shuttlePretiltByType = s
        _liftoutTiltByType = l
        _attachTiltByType = a
    }

    // If a spin-box editor currently has focus, its typed text may not
    // be committed to `value` yet. Moving focus to the combo forces the
    // CustomSpinBox focus-out commit so a subsequent read of `value` is
    // accurate. Guarded so construction-time combo init doesn't steal
    // focus when nothing is being edited.

    function _commitActiveEditor() {
        if (shuttlePretiltAngleSB.activeFocus
                || liftoutTiltAngleSB.activeFocus
                || attachTiltAngleSB.activeFocus) {
            liftoutCalcComboBox.forceActiveFocus()
        }
    }

    // Save the three currently-displayed spin-box values into the store
    // slot for the type the boxes represent (_activeTypeIndex).

    function _saveSpinBoxesToStore() {
        _writeStore(_activeTypeIndex,
                    shuttlePretiltAngleSB.value,
                    liftoutTiltAngleSB.value,
                    attachTiltAngleSB.value)
    }

    // Load a type's stored values into the spin boxes and record that
    // the boxes now represent that type. Imperative `value = ...` does
    // not emit valueModified(), and there is no write-back handler, so
    // this never echoes back into the store.

    function _loadStoreIntoSpinBoxes(idx) {
        shuttlePretiltAngleSB.value = _shuttlePretiltByType[idx]
        liftoutTiltAngleSB.value    = _liftoutTiltByType[idx]
        attachTiltAngleSB.value     = _attachTiltByType[idx]
        _activeTypeIndex = idx
    }

    // Type switch: flush any in-progress edit, save the outgoing type,
    // then load the incoming type. Called from
    // liftoutCalcComboBox.onCurrentIndexChanged.

    function _onTypeChanged(newIndex) {
        if (!liftoutTiltAngleSB || !attachTiltAngleSB
                || !shuttlePretiltAngleSB) {
            return   // spin boxes not constructed yet (startup ordering)
        }
        if (newIndex === _activeTypeIndex) {
            return   // no-op (e.g. construction-time initial signal)
        }
        _commitActiveEditor()
        _saveSpinBoxesToStore()
        _loadStoreIntoSpinBoxes(newIndex)
    }

    // Reset the three inputs to defaults for the selected type only.
    // Store-first, then push to the boxes so the reset persists across
    // later type switches. Other types' remembered values are
    // deliberately left untouched.

    function _resetSelectedType() {
        var idx = liftoutCalcComboBox.currentIndex
        _writeStore(idx,
                    _shuttlePretiltDefaultFor(idx),
                    _liftoutTiltDefaultFor(idx),
                    _attachTiltDefaultFor(idx))
        _loadStoreIntoSpinBoxes(idx)
    }

    // ------------------------------------------------------------------
    // Layout
    // ------------------------------------------------------------------

    GridLayout {

        id: calculatorGrid
        anchors.fill: parent
        rowSpacing: AppConfig.activityFormRowSpacing
        columnSpacing: AppConfig.activityFormColumnSpacing

        // ---- Row 0: Lift-out Combo Box (select type of lift-out) ----

        ComboBox {

            id: liftoutCalcComboBox
            Layout.row: 0
            Layout.columnSpan: 2
            Layout.fillWidth: true
            model: ListModel {
                ListElement { name: "Top-down: -70" }
                ListElement { name: "Planar: -70" }
                ListElement { name: "Planar: 110" }
            }

            // Type switch saves the outgoing type's values and loads
            // the incoming type's values (save-on-leave). It no longer
            // overwrites inputs with defaults. Output Labels recompute
            // automatically via _fibStageTiltAngle.
            onCurrentIndexChanged: root._onTypeChanged(currentIndex)

        }

        // ---- Row 1: Spacer ----

        Item { Layout.row: 1; Layout.preferredHeight: 12 }

        // ---- Row 2: Shuttle Pre-tilt Angle ----

        ToolTippedLabel {

            id: shuttlePretiltAngleLabel
            Layout.row: 2
            Layout.column: 0
            text: "Shuttle Pre-tilt Angle"
            toolTipText: Strings.shuttlePretiltAngleLabelToolTip

        }

        CustomSpinBox {

            id: shuttlePretiltAngleSB
            Layout.row: 2
            Layout.column: 1
            Layout.alignment: Qt.AlignRight
            Layout.preferredWidth: root._spinBoxWidth
            from: AppConfig.shuttlePretiltAngleMin
            to: AppConfig.shuttlePretiltAngleMax
            // Constant seed for frame-one display. Equals
            // _shuttlePretiltByType[0] at construction (both come from
            // shuttlePretiltAngleDefault). After construction the store
            // is authoritative for remembered values; this binding is
            // severed by the first imperative load and is intentionally
            // not relied on again.
            value: AppConfig.shuttlePretiltAngleDefault
            showArrows: true

        }

        // ---- Row 3: Lift-out Tilt Angle ----

        ToolTippedLabel {

            id: liftoutTiltAngleLabel
            Layout.row: 3
            Layout.column: 0
            text: "Lift-out Tilt Angle"
            toolTipText: Strings.liftoutTiltAngleLabelTooltip

        }

        CustomSpinBox {

            id: liftoutTiltAngleSB
            Layout.row: 3
            Layout.column: 1
            Layout.alignment: Qt.AlignRight
            Layout.preferredWidth: root._spinBoxWidth
            from: AppConfig.liftoutTiltAngleMin
            to: AppConfig.liftoutTiltAngleMax
            // Constant seed for frame-one display (combo starts at
            // index 0 = Top-down). Must equal _liftoutTiltByType[0] —
            // both derive from liftoutTiltDefaultTopdownMinus70. After
            // construction the store is authoritative; this binding is
            // severed by the first imperative load and is intentionally
            // not relied on again.
            value: AppConfig.liftoutTiltDefaultTopdownMinus70
            showArrows: true

        }

        // ---- Row 4: Attach Tilt Angle ----

        ToolTippedLabel {

            id: attachTiltAngleLabel
            Layout.row: 4
            Layout.column: 0
            text: "Lift-in Tilt Angle"
            toolTipText: Strings.attachTiltAngleLabelToolTip

        }

        CustomSpinBox {

            id: attachTiltAngleSB
            Layout.row: 4
            Layout.column: 1
            Layout.alignment: Qt.AlignRight
            Layout.preferredWidth: root._spinBoxWidth
            from: AppConfig.attachTiltAngleMin
            to: AppConfig.attachTiltAngleMax
            // Constant seed for frame-one display (combo starts at
            // index 0 = Top-down). Must equal _attachTiltByType[0] —
            // both derive from attachTiltDefaultTopdownMinus70. After
            // construction the store is authoritative; this binding is
            // severed by the first imperative load and is intentionally
            // not relied on again.
            value: AppConfig.attachTiltDefaultTopdownMinus70
            showArrows: true

        }

        // ---- Row 5: Spacer ----

        Item { Layout.row: 5; Layout.preferredHeight: 12 }

        // ---- Row 6: FIB Stage Tilt Angle (calculated) ----

        ToolTippedLabel {

            id: fibStageTiltAngleLabel
            Layout.row: 6
            Layout.column: 0
            text: "Stage Tilt Angle"
            toolTipText: Strings.fibStageTiltAngleLabelToolTip

        }

        Label {

            id: fibStageTiltAngleCalculatedLabel
            Layout.row: 6
            Layout.column: 1
            Layout.alignment: Qt.AlignRight
            text: root._fibStageTiltAngle + "°"

        }

        // ---- Row 7: FIB Milling Angle (-70) (calculated) ----

        ToolTippedLabel {

            id: fibMillingAngleLabel
            Layout.row: 7
            Layout.column: 0
            text: "FIB Milling Angle (-70)"
            toolTipText: Strings.fibMillingAngleLabelToolTip

        }

        Label {

            id: fibMillingAngleCalculatedLabel
            Layout.row: 7
            Layout.column: 1
            Layout.alignment: Qt.AlignRight
            text: root._fibMillingAngleMinus70 + "°"

        }

        // ---- Row 8: SEM Tilt Angle (-70) (calculated) ----

        ToolTippedLabel {

            id: semTiltAngleLabel
            Layout.row: 8
            Layout.column: 0
            text: "SEM Tilt Angle (-70)"
            toolTipText: Strings.semTiltAngleLabelToolTip

        }

        Label {

            id: semTiltAngleCalculatedLabel
            Layout.row: 8
            Layout.column: 1
            Layout.alignment: Qt.AlignRight
            text: root._semTiltAngleMinus70 + "°"

        }

        // ---- Row 9: Spacer ----

        Item { Layout.row: 9; Layout.preferredHeight: 12 }

        // ---- Row 10: Reset Button ----

        RoundButton {

            id: resetButton
            Layout.row: 10
            Layout.column: 0
            Layout.alignment: Qt.AlignLeft
            text: "Reset"
            radius: AppConfig.buttonRadius
            ToolTip.text: Strings.liftoutCalcResetButtonTooltip
            ToolTip.delay: AppConfig.toolTipDelayMs
            ToolTip.timeout: AppConfig.toolTipTimeoutMs
            ToolTip.visible: hovered
            onClicked: root._resetSelectedType()

        }

    }

}
