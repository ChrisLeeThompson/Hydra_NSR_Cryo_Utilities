
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Universal
import QtQuick.Controls.impl
import "../Config"

// Custom Spin Box

// Extended SpinBox with:
//   - optional arrow buttons (showArrows: bool)
//   - scroll wheel increment/decrement
//   - Enter/Return commits and releases focus
//   - dark-theme-appropriate selection colors
//   - optional float value support via `decimals` property
//
// Integer use (decimals: 0, the default):
//   CustomSpinBox {
//       from: 1; to: 999; value: 120
//   }
//   // Read back via: spinBox.value
//
// Float use (decimals > 0):
//   CustomSpinBox {
//       decimals: 2
//       floatFrom: 0.0; floatTo: 10.0; floatValue: 3.14; floatStep: 0.01
//   }
//   // Read back via: spinBox.realValue
//
// Do not mix integer and float APIs on the same instance — pick one per
// call site based on the `decimals` setting.

SpinBox {

    id: root

    // --- Public additions ---

    // Visual
    property bool showArrows: true

    // Float-mode configuration.  Leave decimals: 0 for integer behavior.
    property int decimals: 0
    property real floatFrom: 0.0
    property real floatTo: 100.0
    property real floatValue: 0.0
    property real floatStep: 1.0

    // Read-back for callers when decimals > 0.  For integer use, read `value`.
    readonly property real realValue: value / _factor

    // --- Internal ---

    // Tick factor derived from decimals (1 when integer, 10^decimals otherwise).
    readonly property int _factor: Math.pow(10, decimals)

    // No self-referential bindings; only override when in float mode.
    Binding on from     { value: Math.round(root.floatFrom  * root._factor); when: root.decimals > 0 }
    Binding on to       { value: Math.round(root.floatTo    * root._factor); when: root.decimals > 0 }
    Binding on value    { value: Math.round(root.floatValue * root._factor); when: root.decimals > 0 }
    Binding on stepSize { value: Math.round(root.floatStep  * root._factor); when: root.decimals > 0 }

    // --- Defaults ---
    editable: true

    // --- Display formatting (ticks → string) ---
    textFromValue: function(val, locale) {
        if (decimals > 0) {
            return Number(val / _factor).toLocaleString(locale, 'f', decimals)
        }
        return Number(val).toLocaleString(locale, 'f', 0)
    }

    // --- Input parsing (string → ticks) ---
    valueFromText: function(text, locale) {
        if (decimals > 0) {
            return Math.round(Number.fromLocaleString(locale, text) * _factor)
        }
        return Number.fromLocaleString(locale, text)
    }

    // --- Validator respects decimals ---
    validator: decimals > 0
               ? doubleValidator
               : intValidator

    DoubleValidator {
        id: doubleValidator
        bottom: root.floatFrom
        top: root.floatTo
        decimals: root.decimals
        notation: DoubleValidator.StandardNotation
        locale: root.locale.name
    }

    IntValidator {
        id: intValidator
        bottom: root.from
        top: root.to
        locale: root.locale.name
    }

    // --- Stable editor width ---------------------------------------------
    // Size the editor once to the widest value the range can produce, so the
    // box does not grow/shrink as `value` changes. Sizing is per-instance:
    // each spin box fits its own from..to range, decimals, and font.

    // Optional caller escape hatch: reserve N extra '8'-glyph widths.
    property int minContentChars: 0

    // Extra digit-widths of breathing room beyond the widest value.
    // 0 = snug to the widest value (plus the caret margin in implicitWidth).
    readonly property int _breathingChars: 0

    // The widest string the range can render. We format both endpoints with
    // the control's own textFromValue (so float decimals + locale separators
    // are exact), pick the longer, then widen every digit to '8' so no
    // in-range value can exceed it in a proportional font. Sign and decimal/
    // grouping separators are preserved (the regex targets [0-9] only).
    readonly property string _widestValueText: {
        var lo = root.textFromValue(root.from, root.locale)
        var hi = root.textFromValue(root.to,   root.locale)
        var longest = (hi.length >= lo.length) ? hi : lo
        var widened = longest.replace(/[0-9]/g, "8")
        var extra = new Array(root._breathingChars + root.minContentChars + 1).join("8")
        return widened + extra
    }

    TextMetrics {
        id: _valueMetrics
        font: root.font
        text: root._widestValueText
    }

    // Pin the control's implicit width to the widest in-range value (not the
    // live text). A TextInput's implicitWidth is read-only, so we size the
    // whole control here. leftPadding/rightPadding already include the base
    // padding AND the arrow-indicator widths, so content + leftPadding +
    // rightPadding is the correct width whether arrows are shown or not; the
    // background min still floors very short ranges. +6 px caret/edge margin.
    // (Stable across values: depends only on range/decimals/font/padding.)
    implicitWidth: Math.max(
        implicitBackgroundWidth + leftInset + rightInset,
        Math.ceil(_valueMetrics.advanceWidth) + 6 + leftPadding + rightPadding
    )

    // --- Arrow indicators ---
    // Two responsibilities are folded into these custom delegates:
    //   1. showArrows toggles visibility AND collapses the reserved layout
    //      width (implicitWidth -> 0) so hidden arrows don't reserve space.
    //   2. High-contrast glyph fix: the Universal style hardcodes the arrow
    //      glyph to chromeBlackHighColor (pure black) whenever the control has
    //      active focus, which is unreadable on our dark field. These delegates
    //      reproduce the Universal 6.7 originals (geometry, hover/press fill,
    //      arrow image) but pin the glyph color so the arrows stay legible
    //      whether the box is selected or not — same rationale as the
    //      contentItem fix below.
    // NOTE: do not also assign up.indicator.visible/.width separately — mixing
    // a direct delegate assignment with grouped sub-property assignments on the
    // same property is a QML error ("Cannot assign a value directly to a
    // grouped property").
    up.indicator: Item {
        implicitWidth: root.showArrows ? 28 : 0
        visible: root.showArrows
        height: root.height + 4
        y: -2
        x: root.mirrored ? 0 : root.width - width

        Rectangle {
            x: 2; y: 4
            width: parent.width - 4
            height: parent.height - 8
            color: root.activeFocus ? root.Universal.accent :
                   root.up.pressed ? root.Universal.baseMediumLowColor :
                   root.up.hovered ? root.Universal.baseLowColor : "transparent"
            visible: root.up.pressed || root.up.hovered
            opacity: root.activeFocus && !root.up.pressed ? 0.4 : 1.0
        }

        ColorImage {
            x: (parent.width - width) / 2
            y: (parent.height - height) / 2
            // Bind to this indicator's own (inherited) `enabled`, not
            // root.enabled: the SpinBox template disables the up indicator
            // when value is at the maximum, and disables the whole control
            // when root.enabled is false. Both propagate here, so the arrow
            // dims at the limit AND when the control is disabled — matching
            // the original Universal behavior.
            color: enabled ? AppConfig.universalForeground   // white when actionable
                           : AppConfig.textDisabledColor     // dimmed at limit / disabled
            source: "qrc:/qt-project.org/imports/QtQuick/Controls/Universal/images/"
                    + (root.mirrored ? "left" : "right") + "arrow.png"
        }
    }

    down.indicator: Item {
        implicitWidth: root.showArrows ? 28 : 0
        visible: root.showArrows
        height: root.height + 4
        y: -2
        x: root.mirrored ? root.width - width : 0

        Rectangle {
            x: 2; y: 4
            width: parent.width - 4
            height: parent.height - 8
            color: root.activeFocus ? root.Universal.accent :
                   root.down.pressed ? root.Universal.baseMediumLowColor :
                   root.down.hovered ? root.Universal.baseLowColor : "transparent"
            visible: root.down.pressed || root.down.hovered
            opacity: root.activeFocus && !root.down.pressed ? 0.4 : 1.0
        }

        ColorImage {
            x: (parent.width - width) / 2
            y: (parent.height - height) / 2
            // See up.indicator above: dims at the lower limit (template
            // disables the down indicator at the minimum) and when disabled.
            color: enabled ? AppConfig.universalForeground   // white when actionable
                           : AppConfig.textDisabledColor     // dimmed at limit / disabled
            source: "qrc:/qt-project.org/imports/QtQuick/Controls/Universal/images/"
                    + (root.mirrored ? "right" : "left") + "arrow.png"
        }
    }

    // --- Scroll wheel support ---
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.NoButton
        onWheel: (wheel) => {
            if (!root.enabled) return
            if (!root.activeFocus) {      // ← new guard
                wheel.accepted = false     // let the event bubble up
                return
            }
            // increase()/decrease() are programmatic and do NOT emit
            // valueModified on their own, which silently desyncs the
            // displayed value from consumers that recalculate in
            // onValueModified. The wheel is user interaction, so emit
            // it ourselves - but only when the value actually changed
            // (no emission when already clamped at from/to).
            var before = root.value
            if (wheel.angleDelta.y > 0) {
                root.increase()
            } else if (wheel.angleDelta.y < 0) {
                root.decrease()
            }
            if (root.value !== before) {
                root.valueModified()
            }
        }
    }

    // --- Enter/Return dismisses focus ---
    Keys.onReturnPressed: root.focus = false
    Keys.onEnterPressed: root.focus = false

    // --- Selection colors (the black-on-dark fix) ---
    contentItem: TextInput {
        text: root.displayText
        font: root.font
        color: root.enabled
               ? AppConfig.universalForeground
               : AppConfig.textDisabledColor
        selectionColor: AppConfig.universalAccent
        selectedTextColor: AppConfig.universalBackground
        horizontalAlignment: Qt.AlignHCenter
        verticalAlignment: Qt.AlignVCenter
        readOnly: !root.editable
        validator: root.validator
        inputMethodHints: root.decimals > 0
                          ? Qt.ImhFormattedNumbersOnly
                          : Qt.ImhDigitsOnly
    }

}

